# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for knowledge-base chunker configuration updates."""
import asyncio
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any, AsyncIterator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

import fakeredis.aioredis
from fastapi import HTTPException

from rag_vdb_elasticsearch_test import _FakeClient as _FakeESClient
from rag_vdb_mongodb_test import _FakeMongoClient
from service_knowledge_base_upload_test import (
    _FakeKbManager,
    _FakeVectorStore,
    _make_storage,
)

from agentscope.app._service._access import ResourceAccessService
from agentscope.app._service._knowledge_base import KnowledgeBaseService
from agentscope.app._service._index_sweeper import IndexSweeper
from agentscope.app._service._index_worker import IndexWorker
from agentscope.app.access import DenyAllResourceAccessPolicy
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.rag.blob_store import LocalBlobStore
from agentscope.app.rag.knowledge_base_manager import CollectionPerKbManager
from agentscope.app.rag.knowledge_base_manager._vector_slots import (
    version_knowledge,
)
from agentscope.app.storage import (
    ChunkerConfig,
    EmbeddingModelConfig,
    KnowledgeBaseData,
    KnowledgeBaseRecord,
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
)
from agentscope.message import TextBlock
from agentscope.rag import (
    ApproxTokenChunker,
    Chunk,
    DocumentSummary,
    ElasticsearchStore,
    MongoDBStore,
    TextParser,
)


class _AlternateApproxTokenChunker(ApproxTokenChunker):
    """Second registered type used to verify chunker replacement."""

    chunker_type = "alternate_approx_token"


class _SnapshotESClient(_FakeESClient):
    """Keep durable rows and searchable snapshots separate."""

    def __init__(self) -> None:
        super().__init__()
        self.written: dict[str, dict] = {}
        self.visible: dict[str, dict] = {}
        self.indices.exists.side_effect = lambda index: index in self.written
        self.indices.create.side_effect = self._create
        self.bulk.side_effect = self._bulk
        self.delete_by_query.side_effect = self._delete
        self.search.side_effect = self._search
        self.indices.refresh.side_effect = self.refresh_index

    async def _create(self, index: str, **_kwargs: Any) -> None:
        self.written[index] = {}

    async def _bulk(self, operations: list, refresh: bool) -> dict:
        assert refresh is False
        for op, source in zip(operations[::2], operations[1::2], strict=True):
            action = op["index"]
            self.written[action["_index"]][action["_id"]] = source
        return {"errors": False}

    async def _delete(self, index: str, query: dict, **kwargs: Any) -> dict:
        for key, source in self.visible.get(index, {}).items():
            if source["document_id"] == query["term"]["document_id"]:
                self.written[index].pop(key, None)
        if kwargs["refresh"]:
            await self.refresh_index(index)
        return {"failures": [], "version_conflicts": 0, "timed_out": False}

    async def _search(self, index: str, knn: dict, **kwargs: Any) -> dict:
        excluded = {
            key
            for clause in knn.get("filter", [])
            for omit in clause["bool"]["must_not"]
            for key in omit["terms"]["document_id"]
        }
        hits = [
            {"_score": 1.0, "_source": source}
            for source in self.visible.get(index, {}).values()
            if source["document_id"] not in excluded
        ]
        return {"hits": {"hits": hits[: kwargs["size"]]}}

    async def refresh_index(self, index: str) -> dict:
        """Expose writes and deletes on all shards of one index."""
        self.visible[index] = self.written[index].copy()
        return {"_shards": {"failed": 0}}


class KnowledgeBaseChunkerUpdateTest(IsolatedAsyncioTestCase):
    """Verify configuration persistence and reindex dispatch semantics."""

    # pylint: disable=too-many-public-methods

    async def asyncSetUp(self) -> None:
        """Build the service with in-memory storage and message bus fakes."""
        # Cleaned up by asyncTearDown after the workers finish.
        # pylint: disable-next=consider-using-with
        self._temporary_directory = tempfile.TemporaryDirectory()
        self._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._storage = _make_storage(self._redis)
        self._storage._client = self._redis
        self._bus = InMemoryMessageBus()
        self._manager = _FakeKbManager(
            storage=self._storage,
            vector_store=_FakeVectorStore(),
        )
        self._service = KnowledgeBaseService(
            storage=self._storage,
            knowledge_base_manager=self._manager,
            blob_store=LocalBlobStore(self._temporary_directory.name),
            message_bus=self._bus,
            resource_access_service=ResourceAccessService(
                self._storage,
                DenyAllResourceAccessPolicy(),
            ),
            chunkers=[ApproxTokenChunker, _AlternateApproxTokenChunker],
        )
        self._original_chunker = ChunkerConfig(
            type="approx_token",
            parameters={},
        )
        record = KnowledgeBaseRecord(
            user_id="user-1",
            data=KnowledgeBaseData(
                name="kb",
                description="description",
                embedding_model_config=EmbeddingModelConfig(
                    type="openai_credential",
                    credential_id="cred-1",
                    model="text-embedding-3-small",
                    dimensions=1,
                ),
                chunker_config=self._original_chunker,
                collection_name="kb_collection",
            ),
        )
        self._knowledge_base_id = record.id
        await self._storage.upsert_knowledge_base("user-1", record)

    async def asyncTearDown(self) -> None:
        """Close in-memory resources."""
        await self._bus.aclose()
        await self._redis.aclose()
        self._temporary_directory.cleanup()

    async def _store_document(
        self,
        document_id: str,
        document_status: str,
    ) -> None:
        """Persist one document with deliberately stale result metadata."""
        record = KnowledgeDocumentRecord(
            id=document_id,
            created_at=datetime.now() - timedelta(days=1),
            user_id="user-1",
            knowledge_base_id=self._knowledge_base_id,
            status=document_status,
            data=KnowledgeDocumentData(
                filename=f"{document_id}.txt",
                size=12,
                content_type="text/plain",
                blob_uri=f"local://kb/{document_id}",
                error="old error",
                chunk_count=3,
            ),
        )
        await self._storage.upsert_knowledge_document("user-1", record)

    async def test_legacy_uploads_finish_and_reindex_is_rejected(self) -> None:
        """A legacy writer can finish uploads but cannot rebuild documents."""
        owner, kb = "user-1", self._knowledge_base_id
        update = self._storage.update_knowledge_document_status

        async def legacy_update(
            user_id: str,
            knowledge_base_id: str,
            document_id: str,
            status: str,
            error: str | None = None,
            chunk_count: int | None = None,
        ) -> None:
            await update(
                user_id,
                knowledge_base_id,
                document_id,
                status,
                error=error,
                chunk_count=chunk_count,
            )

        with patch.object(
            self._storage,
            "update_knowledge_document_status",
            legacy_update,
        ):
            # An empty KB can change chunkers before its first upload.
            updated = await self._service.update_knowledge_base(
                owner,
                kb,
                chunker_config=ChunkerConfig(
                    type="approx_token",
                    parameters={"chunk_size": 128, "overlap": 16},
                ),
            )
            self.assertEqual(
                (updated.data.reindex_id, updated.data.reindex_pending),
                (None, False),
            )
            worker = IndexWorker(
                self._storage,
                self._service._blob_store,
                self._manager,
                [TextParser()],
                "worker",
                message_bus=self._bus,
            )
            for expected in ("ready", "error"):
                doc = await self._service.register_document(
                    owner,
                    kb,
                    "note.txt",
                    BytesIO(b"note"),
                    4,
                    "text/plain",
                )
                await self._assert_dispatched([doc.id])
                if expected == "error":
                    with patch.object(
                        worker,
                        "_read_blob",
                        side_effect=OSError("read failed"),
                    ):
                        await worker.process(owner, kb, doc.id)
                else:
                    await worker.process(owner, kb, doc.id)
                row = await self._storage.get_knowledge_document(
                    owner,
                    kb,
                    doc.id,
                )
                self.assertEqual(
                    (row.status, row.data.chunk_count, bool(row.data.error)),
                    (expected, int(expected == "ready"), expected == "error"),
                )
                if expected == "error":
                    await self._service.retry_document(owner, kb, doc.id)
                    await self._assert_dispatched([doc.id])
                    await worker.process(owner, kb, doc.id)
                    row = await self._storage.get_knowledge_document(
                        owner,
                        kb,
                        doc.id,
                    )
                    self.assertEqual(
                        (row.status, row.data.chunk_count),
                        ("ready", 1),
                    )

            before = await self._storage.get_knowledge_base(owner, kb)
            docs = await self._storage.list_knowledge_documents(owner, kb)
            with self.assertRaises(HTTPException) as raised:
                await self._service.update_knowledge_base(
                    owner,
                    kb,
                    chunker_config=self._original_chunker,
                )
            self.assertEqual(raised.exception.status_code, 501)
            self.assertEqual(
                await self._storage.get_knowledge_base(owner, kb),
                before,
            )
            self.assertEqual(
                await self._storage.list_knowledge_documents(owner, kb),
                docs,
            )
            await self._assert_dispatched([])

            # Recover the marker left by an older empty-KB update. Both
            # uploads finished after that update and must remain untouched.
            before.data.reindex_id = "interrupted-empty-batch"
            before.data.reindex_pending = True
            await self._storage.upsert_knowledge_base(owner, before)
            await IndexSweeper(self._storage, self._bus)._sweep_once()
            self.assertEqual(
                await self._storage.list_knowledge_documents(owner, kb),
                docs,
            )
            await self._assert_dispatched([])

    async def test_search_rechecks_exclusions_after_publication(self) -> None:
        """A newly published first choice outranks a full fallback window."""
        owner, kb = "user-1", self._knowledge_base_id
        reader = await self._manager.get_published_knowledge(owner, kb)
        writer = await self._manager.get_write_knowledge(owner, kb)
        chunk = Chunk(
            content=TextBlock(text="note"),
            source="note.txt",
            chunk_index=0,
            total_chunks=1,
        )
        store = writer.vector_store
        search = store.search
        search_excluding = store.search_excluding

        async def ranked_search(*args: Any, **kwargs: Any) -> list:
            hits = await search(*args, **kwargs)
            for hit in hits:
                hit.score = 0.5 if hit.document_id == "other" else 1.0
            return hits

        async def publish_then_search(*args: Any, **kwargs: Any) -> list:
            await self._storage.update_knowledge_document_status(
                owner,
                kb,
                "doc",
                "ready",
                alternate_index=True,
            )
            await writer.delete_document("doc")
            return await search_excluding(*args, **kwargs)

        for layout, top_k in (
            ("legacy", 1),
            ("legacy", 2),
            ("isolated", 1),
            ("isolated", 2),
        ):
            with self.subTest(layout=layout, top_k=top_k):
                await writer.delete_document("other")
                await writer.insert_document([chunk], "other")
                await self._store_document("doc", "ready")
                row = await self._storage.get_knowledge_document(
                    owner,
                    kb,
                    "doc",
                )
                row.data.reindex_id = "legacy-batch"
                row.data.slot_layout = layout
                await self._storage.upsert_knowledge_document(owner, row)
                await writer.delete_document("~doc")
                target, key = (
                    (writer, "~doc")
                    if layout == "legacy"
                    else (version_knowledge(writer), "doc")
                )
                await target.delete_document(key)
                await writer.insert_document([chunk], "doc")
                await target.insert_document([chunk], key)
                with patch.object(
                    store,
                    "search",
                    ranked_search,
                ), patch.object(
                    store,
                    "search_excluding",
                    side_effect=publish_then_search,
                ):
                    hits = await reader.search(["query"], top_k=top_k)
                self.assertEqual(
                    [(h.document_id, h.score) for h in hits],
                    [("doc", 1.0), ("other", 0.5)][:top_k],
                )

    async def test_publish_between_candidate_windows(self) -> None:
        """Refresh a destination window that predates publication."""
        owner, kb = "user-1", self._knowledge_base_id
        primary = await self._manager.get_write_knowledge(owner, kb)
        versions = version_knowledge(primary)
        reader = await self._manager.get_published_knowledge(owner, kb)
        chunk = Chunk(
            content=TextBlock(text="note"),
            source="note.txt",
            chunk_index=0,
            total_chunks=1,
        )
        for key, alternate in (("best", False), ("other", True)):
            await self._store_document(key, "ready")
            row = await self._storage.get_knowledge_document(owner, kb, key)
            row.data.slot_layout = "isolated"
            row.data.alternate_index = alternate
            await self._storage.upsert_knowledge_document(owner, row)
            handle = versions if alternate else primary
            await handle.insert_document([chunk], key)

        store = primary.vector_store
        search = store.search
        switching = False

        async def ranked_search(
            collection: str,
            *args: Any,
            **kwargs: Any,
        ) -> list:
            nonlocal switching
            hits = await search(collection, *args, **kwargs)
            for hit in hits:
                hit.score = 1.0 if hit.document_id == "best" else 0.5
            if switching and collection == versions.collection:
                switching = False
                await versions.insert_document([chunk], "best")
                await self._storage.update_knowledge_document_status(
                    owner,
                    kb,
                    "best",
                    "ready",
                    alternate_index=True,
                )
            return hits

        with patch.object(store, "search", ranked_search):
            results = []
            for switching in (False, True, False):
                hits = await reader.search(["query"], top_k=1)
                results.append([(h.document_id, h.score) for h in hits])
        self.assertEqual(results, [[("best", 1.0)]] * 3)

    async def test_reused_slot_never_returns_retired_text(self) -> None:
        """A cached hit for A cannot represent C at the same rank."""
        owner, kb = "user-1", self._knowledge_base_id
        primary = await self._manager.get_write_knowledge(owner, kb)
        versions = version_knowledge(primary)
        reader = await self._manager.get_published_knowledge(owner, kb)
        await self._store_document("doc", "ready")
        row = await self._storage.get_knowledge_document(owner, kb, "doc")
        row.data.begin_reindex("batch-C")
        await self._storage.upsert_knowledge_document(owner, row)

        async def write(handle: Any, text: str) -> None:
            chunk = Chunk(
                content=TextBlock(text=text),
                source="note",
                chunk_index=0,
                total_chunks=1,
            )
            await handle.delete_document("doc")
            await handle.insert_document([chunk], "doc")

        search = primary.vector_store.search
        switch = False
        keep_previous = True

        async def publish_after_read(
            collection: str,
            *args: Any,
            **kwargs: Any,
        ) -> list:
            nonlocal switch
            hits = await search(collection, *args, **kwargs)
            if switch and collection == versions.collection:
                switch = False
                await write(versions, "C")
                await self._storage.update_knowledge_document_status(
                    owner,
                    kb,
                    "doc",
                    "ready",
                    alternate_index=True,
                )
                if not keep_previous:
                    await primary.delete_document("doc")
            return hits

        with patch.object(primary.vector_store, "search", publish_after_read):
            for keep_previous in (True, False):
                with self.subTest(keep_previous=keep_previous):
                    await self._storage.upsert_knowledge_document(owner, row)
                    await write(versions, "A")
                    await write(primary, "B")
                    texts = []
                    for switch in (False, True, False):
                        hits = await reader.search(["query"], top_k=1)
                        texts.append([hit.chunk.content.text for hit in hits])
                    self.assertEqual(texts, [["B"], ["C"], ["C"]])

    async def _configure_es_chunks(self, size: int) -> None:
        await self._service.update_knowledge_base(
            "user-1",
            self._knowledge_base_id,
            chunker_config=ChunkerConfig(
                type="approx_token",
                parameters={"chunk_size": size, "overlap": 0},
            ),
        )

    async def _es_rebuild_to_two_chunks(
        self,
    ) -> tuple[_SnapshotESClient, IndexWorker, KnowledgeDocumentRecord]:
        """Leave four primary rows unrefreshed and publish two version rows."""
        owner, kb = "user-1", self._knowledge_base_id
        client = _SnapshotESClient()
        store = ElasticsearchStore("http://mock", refresh=False)
        store._client = client
        self._manager._vector_store = store
        worker = IndexWorker(
            self._storage,
            self._service._blob_store,
            self._manager,
            [TextParser()],
            "worker",
            message_bus=self._bus,
        )

        content = b"abcdefghijklmnop"
        await self._configure_es_chunks(1)
        doc = await self._service.register_document(
            owner,
            kb,
            "note.txt",
            BytesIO(content),
            len(content),
            "text/plain",
        )
        key = (owner, kb, doc.id)
        await worker.process(*key)
        reader = await self._manager.get_published_knowledge(owner, kb)
        row = await self._storage.get_knowledge_document(*key)
        self.assertEqual(
            (
                row.status,
                row.data.chunk_count,
                len(client.written[reader.collection]),
            ),
            ("ready", 4, 4),
        )
        client.indices.refresh.assert_not_awaited()
        await self._configure_es_chunks(2)
        await worker.process(*key)
        row = await self._storage.get_knowledge_document(*key)
        self.assertEqual((row.status, row.data.chunk_count), ("ready", 2))
        self.assertFalse(client.visible.get(reader.collection))
        return client, worker, doc

    async def test_elasticsearch_rebuild_reuses_unflushed_index(self) -> None:
        """A reused index must only serve the latest replacement chunks."""
        owner, kb = "user-1", self._knowledge_base_id
        client, worker, doc = await self._es_rebuild_to_two_chunks()
        key = (owner, kb, doc.id)
        reader = await self._manager.get_published_knowledge(owner, kb)
        refreshing = asyncio.Event()
        finish = asyncio.Event()

        async def refresh(index: str) -> dict:
            if len(client.written[index]) == 1:
                refreshing.set()
                await finish.wait()
            return await client.refresh_index(index)

        client.indices.refresh.side_effect = refresh
        await self._configure_es_chunks(4)
        task = asyncio.create_task(worker.process(*key))
        try:
            await asyncio.wait_for(refreshing.wait(), 3)
            row = await self._storage.get_knowledge_document(*key)
            hits = await reader.search(["query"], top_k=10)
            self.assertEqual(
                (
                    row.status,
                    row.data.alternate_index,
                    [h.chunk.content.text for h in hits],
                ),
                ("indexing", True, ["abcdefgh", "ijklmnop"]),
            )
        finally:
            finish.set()
            await asyncio.wait_for(task, 3)
        row = await self._storage.get_knowledge_document(*key)
        hits = await reader.search(["query"], top_k=10)
        self.assertEqual(
            (
                row.status,
                row.data.alternate_index,
                row.data.chunk_count,
                [(h.chunk.chunk_index, h.chunk.content.text) for h in hits],
            ),
            ("ready", False, 1, [(0, "abcdefghijklmnop")]),
        )

        # A failed pre-delete refresh must leave both slots untouched.
        await self._configure_es_chunks(2)
        client.delete_by_query.reset_mock()
        client.bulk.reset_mock()
        client.indices.refresh.side_effect = None
        client.indices.refresh.return_value = {"_shards": {"failed": 1}}
        await worker.process(*key)
        row = await self._storage.get_knowledge_document(*key)
        self.assertEqual(
            (row.status, row.data.alternate_index, row.data.chunk_count),
            ("error", False, 1),
        )
        client.delete_by_query.assert_not_awaited()
        client.bulk.assert_not_awaited()
        self.assertEqual(await reader.search(["query"], top_k=10), hits)

    async def test_deleted_es_chunks_stay_deleted_after_refresh(self) -> None:
        """Refreshing after deletion must never expose the original chunks."""
        owner, kb = "user-1", self._knowledge_base_id
        client, _, doc = await self._es_rebuild_to_two_chunks()
        key = (owner, kb, doc.id)
        hits = await self._service.search(owner, kb, "query", top_k=10)
        self.assertEqual(
            [h.chunk.content.text for h in hits],
            ["abcdefgh", "ijklmnop"],
        )
        await self._service.delete_document(*key)
        immediate = await self._service.search(owner, kb, "query", top_k=10)
        for index in client.written:
            await client.indices.refresh(index=index)
        self.assertEqual(
            (
                await self._storage.get_knowledge_document(*key),
                await self._service._blob_store.exists(doc.data.blob_uri),
                immediate,
                await self._service.search(owner, kb, "query", top_k=10),
            ),
            (None, False, [], []),
        )

    async def test_partial_es_delete_can_be_retried(self) -> None:
        """Keep the record and blob until both slots are visibly empty."""
        owner, kb = "user-1", self._knowledge_base_id
        client, _, doc = await self._es_rebuild_to_two_chunks()
        key = (owner, kb, doc.id)
        primary = await self._manager.get_write_knowledge(owner, kb)
        versions = version_knowledge(primary)
        delete = client.delete_by_query.side_effect

        async def partial_delete(index: str, **kwargs: Any) -> dict:
            if index == primary.collection:
                return {"failures": [], "version_conflicts": 1}
            return await delete(index=index, **kwargs)

        client.delete_by_query.side_effect = partial_delete
        with self.assertRaisesRegex(RuntimeError, "delet"):
            await self._service.delete_document(*key)
        self.assertEqual(
            (
                (await self._storage.get_knowledge_document(*key)).id,
                await self._service._blob_store.exists(doc.data.blob_uri),
                client.visible[versions.collection],
            ),
            (doc.id, True, {}),
        )
        client.delete_by_query.side_effect = delete

        async def fail_final_refresh(index: str) -> dict:
            if index == primary.collection and not client.written[index]:
                return {"_shards": {"failed": 1}}
            return await client.refresh_index(index)

        with patch.object(
            client.indices,
            "refresh",
            side_effect=fail_final_refresh,
        ):
            with self.assertRaisesRegex(RuntimeError, "refresh"):
                await self._service.delete_document(*key)
        self.assertEqual(
            (
                (await self._storage.get_knowledge_document(*key)).id,
                await self._service._blob_store.exists(doc.data.blob_uri),
            ),
            (doc.id, True),
        )
        await self._service.delete_document(*key)
        for index in client.written:
            await client.indices.refresh(index=index)
        self.assertEqual(
            (
                await self._storage.get_knowledge_document(*key),
                await self._service._blob_store.exists(doc.data.blob_uri),
                await self._service.search(owner, kb, "query", top_k=10),
            ),
            (None, False, []),
        )

    def _mongo_worker(self) -> tuple[MongoDBStore, IndexWorker]:
        store = MongoDBStore("mongodb://mock", "test-db")
        store._client = _FakeMongoClient("test-db")
        self._manager._vector_store = store
        return store, IndexWorker(
            self._storage,
            self._service._blob_store,
            self._manager,
            [TextParser()],
            "worker",
            message_bus=self._bus,
        )

    async def test_mongo_index_build_failure_and_retry(self) -> None:
        """Keep serving the old upload until the replacement index is ready."""
        owner, kb = "user-1", self._knowledge_base_id
        store, worker = self._mongo_worker()
        doc = await self._service.register_document(
            owner,
            kb,
            "upload.txt",
            BytesIO(b"upload"),
            6,
            "text/plain",
        )
        await worker.process(owner, kb, doc.id)
        reader = await self._manager.get_published_knowledge(owner, kb)
        before = await reader.search(["query"])
        self.assertEqual([h.document_id for h in before], [doc.id])
        await self._service.update_knowledge_base(
            owner,
            kb,
            chunker_config=ChunkerConfig(
                type="approx_token",
                parameters={"chunk_size": 64, "overlap": 0},
            ),
        )
        collection_type = type(store._col(reader.collection))
        with patch.object(
            collection_type,
            "create_search_index",
            side_effect=RuntimeError("build failed"),
        ) as create:
            await worker.process(owner, kb, doc.id)
            row = await self._storage.get_knowledge_document(owner, kb, doc.id)
            self.assertEqual(
                (row.status, row.data.alternate_index),
                ("error", False),
            )
            self.assertEqual(await reader.search(["query"]), before)
            self.assertEqual(create.await_count, 1)

        await self._service.retry_document(owner, kb, doc.id)
        companion = store._col(version_knowledge(reader).collection)
        create_index = companion.create_search_index
        named_index = store._named_index

        async def failed_index(name: str) -> dict | None:
            if name == companion._name:
                return {"status": "FAILED", "queryable": False}
            return await named_index(name)

        with patch.object(store, "_named_index", failed_index):
            await worker.process(owner, kb, doc.id)
            row = await self._storage.get_knowledge_document(owner, kb, doc.id)
            self.assertEqual(
                (row.status, row.data.alternate_index),
                ("error", False),
            )
            self.assertIn(companion._name, row.data.error)
            self.assertIn("vector_index", row.data.error)
            self.assertIn("repair or rebuild", row.data.error)
            self.assertIn("retry", row.data.error)
            self.assertEqual(await reader.search(["query"]), before)
        await self._service.retry_document(owner, kb, doc.id)
        building = asyncio.Event()

        async def start_build(model: Any) -> None:
            await create_index(model)
            companion._index_queryable = False
            building.set()

        with patch.object(companion, "create_search_index", start_build):
            task = asyncio.create_task(worker.process(owner, kb, doc.id))
            try:
                await asyncio.wait_for(building.wait(), 5)
                row = await self._storage.get_knowledge_document(
                    owner,
                    kb,
                    doc.id,
                )
                self.assertEqual(
                    (row.status, row.data.alternate_index),
                    ("indexing", False),
                )
                self.assertEqual(await reader.search(["query"]), before)
            finally:
                companion._index_queryable = True
                await asyncio.wait_for(task, 5)
        row = await self._storage.get_knowledge_document(owner, kb, doc.id)
        self.assertEqual(
            (row.status, row.data.alternate_index),
            ("ready", True),
        )
        self.assertEqual(
            [h.chunk.content.text for h in await reader.search(["query"])],
            ["upload"],
        )
        # A missing index on a published companion must be repaired on read.
        companion.indexes.clear()
        with patch.object(
            companion,
            "create_search_index",
            wraps=companion.create_search_index,
        ) as repair:
            hits = await reader.search(["query"])
        self.assertEqual(
            ([h.document_id for h in hits], repair.await_count),
            ([doc.id], 1),
        )

    async def test_mongo_rebuild_waits_for_every_new_search_hit(self) -> None:
        """Reused slots may still return an entire previous generation."""
        owner, kb = "user-1", self._knowledge_base_id
        store, worker = self._mongo_worker()

        async def configure(size: int) -> None:
            await self._service.update_knowledge_base(
                owner,
                kb,
                chunker_config=ChunkerConfig(
                    type="approx_token",
                    parameters={"chunk_size": size, "overlap": 0},
                ),
            )

        await configure(2)
        content = b"abcdefghijklmnop"
        doc = await self._service.register_document(
            owner,
            kb,
            "note.txt",
            BytesIO(content),
            len(content),
            "text/plain",
        )
        await worker.process(owner, kb, doc.id)
        reader = await self._manager.get_published_knowledge(owner, kb)
        target = store._col(reader.collection)
        stale = dict(target._docs)
        self.assertEqual(len(stale), 2)
        await configure(4)
        await worker.process(owner, kb, doc.id)
        previous = await reader.search(["query"])
        self.assertEqual(
            [hit.chunk.content.text for hit in previous],
            [content.decode()],
        )
        await configure(2)
        target.search_snapshot = stale
        aggregate = target.aggregate
        probes: asyncio.Queue = asyncio.Queue()
        proceed: asyncio.Queue = asyncio.Queue()

        async def gate(pipeline: list) -> Any:
            cursor = await aggregate(pipeline)
            if pipeline[-1] == {"$project": {"_id": 1}}:
                probes.put_nowait(True)
                await proceed.get()
            return cursor

        with patch.object(target, "aggregate", gate):
            task = asyncio.create_task(worker.process(owner, kb, doc.id))
            try:
                for visible in (1, 2):
                    await asyncio.wait_for(probes.get(), 3)
                    row = await self._storage.get_knowledge_document(
                        owner,
                        kb,
                        doc.id,
                    )
                    self.assertEqual(
                        (row.status, row.data.alternate_index),
                        ("indexing", True),
                    )
                    self.assertEqual(await reader.search(["query"]), previous)
                    target.search_snapshot = dict(
                        list(target._docs.items())[:visible],
                    )
                    proceed.put_nowait(True)
                await asyncio.wait_for(probes.get(), 3)
                proceed.put_nowait(True)
                await asyncio.wait_for(task, 3)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        row = await self._storage.get_knowledge_document(owner, kb, doc.id)
        self.assertEqual(
            (row.status, row.data.alternate_index, row.data.chunk_count),
            ("ready", False, 2),
        )
        self.assertEqual(
            [hit.chunk.content.text for hit in await reader.search(["query"])],
            ["abcdefgh", "ijklmnop"],
        )

    async def test_mongo_visibility_timeout_and_lease_takeover(self) -> None:
        """Keep the old slot when a probe times out or loses its lease."""
        owner, kb = "user-1", self._knowledge_base_id
        store, worker = self._mongo_worker()
        doc = await self._service.register_document(
            owner,
            kb,
            "note.txt",
            BytesIO(b"note"),
            4,
            "text/plain",
        )
        key = (owner, kb, doc.id)
        await worker.process(*key)
        reader = await self._manager.get_published_knowledge(owner, kb)
        previous = await reader.search(["query"])
        self.assertEqual([hit.document_id for hit in previous], [doc.id])
        await self._service.update_knowledge_base(
            owner,
            kb,
            chunker_config=ChunkerConfig(
                type="approx_token",
                parameters={"chunk_size": 64, "overlap": 0},
            ),
        )
        target = store._col(version_knowledge(reader).collection)
        target.search_snapshot = {}
        generations = []
        write = target.bulk_write

        async def track_write(*args: Any, **kwargs: Any) -> None:
            await write(*args, **kwargs)
            generations.append(set(target._docs))

        aggregate = target.aggregate

        async def take_lease(pipeline: list) -> Any:
            cursor = await aggregate(pipeline)
            if pipeline[-1] == {"$project": {"_id": 1}}:
                row = await self._storage.get_knowledge_document(*key)
                await self._storage.release_knowledge_document_lease(
                    *key,
                    row.processing_node,
                )
                acquired = (
                    await self._storage.acquire_knowledge_document_lease(
                        *key,
                        "new-owner",
                        timedelta(minutes=5),
                    )
                )
                self.assertTrue(acquired)
            return cursor

        with patch.object(target, "bulk_write", track_write):
            with patch.object(store, "_publication_timeout", 0.05):
                await worker.process(*key)
            row = await self._storage.get_knowledge_document(*key)
            self.assertEqual(
                (row.status, row.data.alternate_index, row.data.chunk_count),
                ("error", False, 1),
            )
            self.assertIn(
                "did not expose the replacement chunks",
                row.data.error,
            )
            self.assertEqual(await reader.search(["query"]), previous)
            await self._service.retry_document(*key)
            target.search_snapshot = None
            with patch.object(target, "aggregate", take_lease):
                await worker.process(*key)
            row = await self._storage.get_knowledge_document(*key)
            self.assertEqual(
                (row.status, row.data.alternate_index, row.processing_node),
                ("indexing", False, "new-owner"),
            )
            self.assertEqual(await reader.search(["query"]), previous)
            await self._storage.release_knowledge_document_lease(
                *key,
                "new-owner",
            )
            await worker.process(*key)
        row = await self._storage.get_knowledge_document(*key)
        self.assertEqual(
            (row.status, row.data.alternate_index, row.data.chunk_count),
            ("ready", True, 1),
        )
        self.assertEqual([len(ids) for ids in generations], [1, 1, 1])
        self.assertEqual(len(set.union(*generations)), 3)
        hits = await reader.search(["query"])
        self.assertEqual(
            [(h.document_id, h.chunk.content.text) for h in hits],
            [(doc.id, "note")],
        )

    async def test_sdk_tilde_ids_are_preserved(self) -> None:
        """SDK IDs stay distinct on every published read interface."""
        reader = await self._manager.get_published_knowledge(
            "user-1",
            self._knowledge_base_id,
        )
        ids = ["notes", "~notes", "~~notes"]
        summaries = []
        for doc_id in ids:
            chunk = Chunk(
                content=TextBlock(text=doc_id),
                source=doc_id,
                chunk_index=0,
                total_chunks=1,
            )
            await reader.insert_document([chunk], doc_id)
            self.assertEqual(await reader.list_chunks(doc_id), [chunk])
            summaries.append(
                DocumentSummary(
                    document_id=doc_id,
                    source=doc_id,
                    chunk_count=1,
                ),
            )
        hits = await reader.search(["query"])
        self.assertEqual([h.document_id for h in hits], ids[::-1])
        with patch.object(
            reader.vector_store,
            "list_documents",
            return_value=summaries,
        ):
            self.assertEqual(await reader.list_documents(), summaries)

    async def test_rebuilt_documents_do_not_exhaust_the_old_collection(
        self,
    ) -> None:
        """An equally scoring retired copy cannot displace the active hit."""
        owner, kb = "user-1", self._knowledge_base_id
        primary = await self._manager.get_write_knowledge(owner, kb)
        versions = version_knowledge(primary)
        reader = await self._manager.get_published_knowledge(owner, kb)
        sdk = Chunk(
            content=TextBlock(text="SDK"),
            source="SDK",
            chunk_index=0,
            total_chunks=1,
        )
        await primary.insert_document([sdk], "SDK")
        for index in range(24):
            key = f"doc-{index}"
            await self._store_document(key, "ready")
            row = await self._storage.get_knowledge_document(owner, kb, key)
            row.data.slot_layout = "isolated"
            row.data.alternate_index = True
            await self._storage.upsert_knowledge_document(owner, row)
            chunk = Chunk(
                content=TextBlock(text=key),
                source=key,
                chunk_index=0,
                total_chunks=1,
            )
            await primary.insert_document([chunk], key)
            await versions.insert_document([chunk], key)
        store = primary.vector_store
        with patch.object(
            store,
            "search",
            wraps=store.search,
        ) as searches, patch.object(
            self._storage,
            "get_knowledge_document",
            wraps=self._storage.get_knowledge_document,
        ) as reads, patch.object(
            self._storage,
            "list_knowledge_documents",
            wraps=self._storage.list_knowledge_documents,
        ) as listings:
            hits = await reader.search(["query"], top_k=1)
        self.assertEqual(
            (
                [h.document_id for h in hits],
                searches.await_count,
                reads.await_count,
                listings.await_count,
            ),
            # Two windows plus one confirmation of the published window.
            (["doc-23"], 3, 2, 0),
        )

        search = store.search

        async def scored_search(
            collection: str,
            *args: Any,
            **kwargs: Any,
        ) -> list:
            hits = await search(collection, *args, **kwargs)
            if collection == primary.collection:
                for hit in hits:
                    hit.score = 2.0 if hit.document_id == "SDK" else 3.0
            return hits

        # The old scores now exceed the best published score. A live SDK
        # document behind all 24 retired uploads must still win.
        with patch.object(store, "search", scored_search), patch.object(
            reader,
            "_candidates",
            wraps=reader._candidates,
        ) as rounds:
            hits = await reader.search(["query"], top_k=1)
        self.assertEqual(
            ([(h.document_id, h.score) for h in hits], rounds.await_count),
            ([("SDK", 2.0)], 3),
        )

    async def test_sdk_id_survives_hosted_rebuilds_and_deletion(self) -> None:
        """A caller may name a document after another upload's old slot."""
        owner, kb = "user-1", self._knowledge_base_id
        doc = await self._service.register_document(
            owner,
            kb,
            "upload.txt",
            BytesIO(b"upload"),
            6,
            "text/plain",
        )
        worker = IndexWorker(
            self._storage,
            self._service._blob_store,
            self._manager,
            [TextParser()],
            "worker",
            message_bus=self._bus,
        )
        await worker.process(owner, kb, doc.id)
        reader = await self._manager.get_published_knowledge(owner, kb)
        sdk_id = f"~{doc.id}"
        sdk = Chunk(
            content=TextBlock(text="SDK"),
            source="SDK",
            chunk_index=0,
            total_chunks=1,
        )
        self.assertEqual(await reader.insert_document([sdk], sdk_id), sdk_id)
        for size in (64, 32):
            await self._service.update_knowledge_base(
                owner,
                kb,
                chunker_config=ChunkerConfig(
                    type="approx_token",
                    parameters={"chunk_size": size, "overlap": 0},
                ),
            )
            await worker.process(owner, kb, doc.id)
            self.assertEqual(await reader.list_chunks(sdk_id), [sdk])
            self.assertEqual(
                {
                    d.document_id: d.source
                    for d in await reader.list_documents()
                },
                {doc.id: "upload.txt", sdk_id: "SDK"},
            )
            self.assertEqual(
                {
                    h.document_id: h.chunk.content.text
                    for h in await reader.search(["query"])
                },
                {doc.id: "upload", sdk_id: "SDK"},
            )
        await self._service.delete_document(owner, kb, doc.id)
        self.assertEqual(await reader.list_chunks(sdk_id), [sdk])
        await reader.delete_document(sdk_id)
        self.assertEqual(await reader.search(["query"]), [])
        manager = CollectionPerKbManager(self._storage, reader.vector_store)
        self.assertTrue(await manager.delete_knowledge_base(owner, kb))
        self.assertEqual(reader.vector_store._collections, {})

    async def test_legacy_published_alias_and_sdk_shadow(self) -> None:
        """Keep an old published slot readable alongside an SDK document."""
        owner, kb = "user-1", self._knowledge_base_id
        writer = await self._manager.get_write_knowledge(owner, kb)
        reader = await self._manager.get_published_knowledge(owner, kb)
        await self._store_document("doc", "ready")
        row = await self._storage.get_knowledge_document(owner, kb, "doc")
        row.data.alternate_index = True
        row.data.reindex_id = "old-batch"
        row.data.blob_uri = await self._service._blob_store.write_stream(
            key="doc",
            stream=BytesIO(b"updated"),
        )
        await self._storage.upsert_knowledge_document(owner, row)
        old = Chunk(
            content=TextBlock(text="old"),
            source="old",
            chunk_index=0,
            total_chunks=1,
        )
        sdk = old.model_copy(update={"content": TextBlock(text="SDK")})
        await writer.insert_document([old], "~doc")
        await reader.insert_document([sdk], "~doc")
        self.assertEqual(await reader.list_chunks("doc"), [old])
        self.assertEqual(await reader.list_chunks("~doc"), [sdk])
        await reader.delete_document("~doc")
        self.assertEqual(await reader.list_chunks("doc"), [old])
        await reader.insert_document([sdk], "~doc")

        worker = IndexWorker(
            self._storage,
            self._service._blob_store,
            self._manager,
            [TextParser()],
            "worker",
            message_bus=self._bus,
        )
        for size in (64, 32):
            await self._service.update_knowledge_base(
                owner,
                kb,
                chunker_config=ChunkerConfig(
                    type="approx_token",
                    parameters={"chunk_size": size, "overlap": 0},
                ),
            )
            await worker.process(owner, kb, "doc")
            self.assertEqual(
                {
                    h.document_id: h.chunk.content.text
                    for h in await reader.search(["query"])
                },
                {"doc": "updated", "~doc": "SDK"},
            )
        await self._service.delete_document(owner, kb, "doc")
        self.assertEqual(await writer.list_chunks("~doc"), [])
        self.assertEqual(await reader.list_chunks("~doc"), [sdk])
        self.assertEqual(
            [d.document_id for d in await reader.list_documents()],
            ["~doc"],
        )
        await reader.delete_document("~doc")
        self.assertEqual(await reader.search(["query"]), [])

    async def test_delete_legacy_alias_by_recorded_ownership(self) -> None:
        """Only the upload's known primary alias is eligible for deletion."""
        owner, kb = "user-1", self._knowledge_base_id
        writer = await self._manager.get_write_knowledge(owner, kb)
        reader = await self._manager.get_published_knowledge(owner, kb)
        for layout in ("legacy", "migrated"):
            key = layout
            alias = f"~{key}"
            await self._store_document(key, "ready")
            row = await self._storage.get_knowledge_document(owner, kb, key)
            row.data.slot_layout = layout
            row.data.alternate_index = True
            await self._storage.upsert_knowledge_document(owner, row)
            old = Chunk(
                content=TextBlock(text="old"),
                source=key,
                chunk_index=0,
                total_chunks=1,
            )
            sdk = old.model_copy(update={"content": TextBlock(text="SDK")})
            await writer.insert_document([old], alias)
            await reader.insert_document([sdk], alias)
            await self._service.delete_document(owner, kb, key)
            self.assertEqual(await reader.list_chunks(alias), [sdk])
            self.assertEqual(
                await writer.list_chunks(alias),
                [] if layout == "legacy" else [old],
            )

    async def test_partial_replacement_keeps_both_documents_searchable(
        self,
    ) -> None:
        """Publish each full replacement and retry only the failed document."""
        owner, kb = "user-1", self._knowledge_base_id
        worker = IndexWorker(
            message_bus=self._bus,
            storage=self._storage,
            blob_store=self._service._blob_store,
            knowledge_base_manager=self._manager,
            parsers=[TextParser()],
            node_id="worker",
        )
        for doc_id in ("a", "b"):
            await self._store_document(doc_id, "pending")
            row = await self._storage.get_knowledge_document(owner, kb, doc_id)
            row.data.blob_uri = await self._service._blob_store.write_stream(
                key=doc_id,
                stream=BytesIO((doc_id * 256).encode()),
            )
            await self._storage.upsert_knowledge_document(owner, row)
            await worker.process(owner, kb, doc_id)

        reader = await self._manager.get_published_knowledge(owner, kb)
        await self._service.update_knowledge_base(
            owner,
            kb,
            chunker_config=ChunkerConfig(
                type="approx_token",
                parameters={"chunk_size": 4, "overlap": 0},
            ),
        )
        await self._assert_dispatched(["a", "b"])
        await worker.process(owner, kb, "b")
        store = self._manager._vector_store
        insert = store.insert

        async def partial_insert(collection: str, records: list) -> None:
            await insert(collection, records[:1])
            # The staging vector ranks first. A top-1 query must look past it.
            hits = await reader.search(["query"], top_k=1)
            self.assertEqual([hit.document_id for hit in hits], ["b"])
            old = await reader.list_chunks("a")
            self.assertEqual([c.content.text for c in old], ["a" * 256])
            raise RuntimeError("partial write")

        with patch.object(store, "insert", side_effect=partial_insert):
            await worker.process(owner, kb, "a")

        a, b = [
            await self._storage.get_knowledge_document(owner, kb, doc_id)
            for doc_id in ("a", "b")
        ]
        self.assertEqual(
            [
                (r.status, r.data.chunk_count, r.data.alternate_index)
                for r in (a, b)
            ],
            [("error", 1, False), ("ready", 16, True)],
        )
        self.assertEqual(a.data.error, "RuntimeError: partial write")
        hits = await reader.search(["query"], top_k=32)
        self.assertEqual(
            [(hit.document_id, hit.chunk.content.text) for hit in hits],
            [("b", "b" * 16)] * 16 + [("a", "a" * 256)],
        )
        await self._service.retry_document(owner, kb, "a")
        with self.assertRaises(HTTPException) as duplicate:
            await self._service.retry_document(owner, kb, "a")
        self.assertEqual(duplicate.exception.status_code, 409)
        await self._assert_dispatched(["a"])
        await worker.process(owner, kb, "a")
        self.assertEqual(
            [len(await reader.list_chunks(doc_id)) for doc_id in ("a", "b")],
            [16, 16],
        )
        self.assertEqual(
            await self._storage.get_knowledge_document(owner, kb, "b"),
            b,
        )

    async def test_cancelled_embedding_recovers_after_lease_expiry(
        self,
    ) -> None:
        """Allow edits during embedding and recover on worker restart."""
        owner, kb = "user-1", self._knowledge_base_id
        doc = await self._service.register_document(
            owner,
            kb,
            "note.txt",
            BytesIO(b"note"),
            4,
            "text/plain",
        )
        await self._assert_dispatched([doc.id])
        raw = await self._manager.get_write_knowledge(owner, kb)
        entered = asyncio.Event()

        async def blocked_embedding(*args: Any, **kwargs: Any) -> None:
            del args, kwargs
            entered.set()
            await asyncio.Event().wait()

        worker = IndexWorker(
            message_bus=self._bus,
            storage=self._storage,
            blob_store=self._service._blob_store,
            knowledge_base_manager=self._manager,
            parsers=[TextParser()],
            node_id="worker",
        )
        with (
            patch.object(
                self._manager,
                "get_write_knowledge",
                return_value=raw,
            ),
            patch.object(
                raw,
                "prepare_document",
                side_effect=blocked_embedding,
            ),
        ):
            task = asyncio.create_task(worker.process(owner, kb, doc.id))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                updated = await asyncio.wait_for(
                    self._service.update_knowledge_base(owner, kb, name="new"),
                    timeout=2,
                )
                self.assertEqual(updated.data.name, "new")
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        row = await self._storage.get_knowledge_document(owner, kb, doc.id)
        self.assertEqual(row.status, "indexing")
        self.assertIsNotNone(row.processing_node)
        row.lease_expires_at = datetime.now() - timedelta(seconds=1)
        await self._storage.upsert_knowledge_document(owner, row)
        await IndexSweeper(self._storage, self._bus)._sweep_once()
        await self._assert_dispatched([doc.id])
        await worker.process(owner, kb, doc.id)
        row = await self._storage.get_knowledge_document(owner, kb, doc.id)
        self.assertEqual((row.status, row.data.chunk_count), ("ready", 1))

    async def test_empty_knowledge_base_updates_without_queueing(self) -> None:
        """An empty knowledge base only needs a metadata update."""
        requested = ChunkerConfig(
            type="approx_token",
            parameters={"chunk_size": 128, "overlap": 16},
        )

        updated = await self._service.update_knowledge_base(
            user_id="user-1",
            knowledge_base_id=self._knowledge_base_id,
            chunker_config=requested,
        )
        queued = await self._bus.queue_drain(
            MessageBusKeys.index_tasks_queue(),
        )

        self.assertEqual(updated.data.chunker_config, requested)
        self.assertEqual(queued, [])

    async def test_changed_chunker_resets_and_queues_terminal_documents(
        self,
    ) -> None:
        """Ready and failed documents both enter a clean reindex pass."""
        await self._store_document("doc-ready", "ready")
        await self._store_document("doc-error", "error")
        requested = ChunkerConfig(
            type="alternate_approx_token",
            parameters={"chunk_size": 64, "overlap": 8},
        )

        await self._service.update_knowledge_base(
            user_id="user-1",
            knowledge_base_id=self._knowledge_base_id,
            chunker_config=requested,
        )
        documents = await self._storage.list_knowledge_documents(
            "user-1",
            self._knowledge_base_id,
        )
        queued = await self._bus.queue_drain(
            MessageBusKeys.index_tasks_queue(),
        )

        document_state = sorted(
            (
                document.id,
                document.status,
                document.processing_node,
                document.lease_expires_at,
                document.data.error,
                document.data.chunk_count,
            )
            for document in documents
        )
        self.assertEqual(
            document_state,
            [
                ("doc-error", "pending", None, None, None, 3),
                ("doc-ready", "pending", None, None, None, 3),
            ],
        )
        self.assertEqual(
            sorted(
                (payload for _, payload in queued),
                key=lambda item: item["document_id"],
            ),
            [
                {
                    "user_id": "user-1",
                    "knowledge_base_id": self._knowledge_base_id,
                    "document_id": "doc-error",
                },
                {
                    "user_id": "user-1",
                    "knowledge_base_id": self._knowledge_base_id,
                    "document_id": "doc-ready",
                },
            ],
        )

    async def test_reindex_persists_each_document_before_enqueue(
        self,
    ) -> None:
        """Each reset is immediately followed by its queue operation."""
        await self._store_document("doc-ready", "ready")
        await self._store_document("doc-error", "error")
        events: list[tuple[str, str]] = []
        original_upsert = self._storage.upsert_knowledge_document
        original_queue_push = self._bus.queue_push

        async def tracking_upsert(
            user_id: str,
            record: KnowledgeDocumentRecord,
        ) -> KnowledgeDocumentRecord:
            events.append(("upsert", record.id))
            return await original_upsert(user_id, record)

        async def tracking_queue_push(
            key: str,
            payload: dict,
        ) -> None:
            if key == MessageBusKeys.index_tasks_queue():
                events.append(("enqueue", payload["document_id"]))
            await original_queue_push(key, payload)

        with (
            patch.object(
                self._storage,
                "upsert_knowledge_document",
                new=tracking_upsert,
            ),
            patch.object(
                self._bus,
                "queue_push",
                new=tracking_queue_push,
            ),
        ):
            await self._service.update_knowledge_base(
                user_id="user-1",
                knowledge_base_id=self._knowledge_base_id,
                chunker_config=ChunkerConfig(
                    type="alternate_approx_token",
                    parameters={},
                ),
            )

        paired_events = sorted(
            (events[index], events[index + 1])
            for index in range(0, len(events), 2)
        )
        self.assertEqual(
            paired_events,
            [
                (
                    ("upsert", "doc-error"),
                    ("enqueue", "doc-error"),
                ),
                (
                    ("upsert", "doc-ready"),
                    ("enqueue", "doc-ready"),
                ),
            ],
        )

    async def test_upload_waits_for_chunker_update_lock(self) -> None:
        """A document cannot enter storage during a chunker update."""
        await self._store_document("doc-ready", "ready")
        update_entered = asyncio.Event()
        release_update = asyncio.Event()
        blob_written = asyncio.Event()
        original_update = self._manager.update_knowledge_base
        original_write_stream = self._service._blob_store.write_stream

        async def blocking_update(
            **kwargs: Any,
        ) -> KnowledgeBaseRecord | None:
            update_entered.set()
            await release_update.wait()
            return await original_update(**kwargs)

        async def tracking_write_stream(**kwargs: Any) -> str:
            blob_uri = await original_write_stream(**kwargs)
            blob_written.set()
            return blob_uri

        with (
            patch.object(
                self._manager,
                "update_knowledge_base",
                new=blocking_update,
            ),
            patch.object(
                self._service._blob_store,
                "write_stream",
                new=tracking_write_stream,
            ),
        ):
            update_task = asyncio.create_task(
                self._service.update_knowledge_base(
                    user_id="user-1",
                    knowledge_base_id=self._knowledge_base_id,
                    chunker_config=ChunkerConfig(
                        type="alternate_approx_token",
                        parameters={},
                    ),
                ),
            )
            await asyncio.wait_for(update_entered.wait(), timeout=1.0)
            upload_task = asyncio.create_task(
                self._service.register_document(
                    user_id="user-1",
                    knowledge_base_id=self._knowledge_base_id,
                    filename="concurrent.txt",
                    stream=BytesIO(b"content"),
                    size=7,
                    content_type="text/plain",
                ),
            )
            await asyncio.wait_for(blob_written.wait(), timeout=1.0)
            await asyncio.sleep(0)
            documents_during_update = (
                await self._storage.list_knowledge_documents(
                    "user-1",
                    self._knowledge_base_id,
                )
            )
            self.assertEqual(
                [document.id for document in documents_during_update],
                ["doc-ready"],
            )
            self.assertFalse(upload_task.done())

            release_update.set()
            await update_task
            uploaded = await upload_task

        knowledge_base = await self._storage.get_knowledge_base(
            "user-1",
            self._knowledge_base_id,
        )
        documents = await self._storage.list_knowledge_documents(
            "user-1",
            self._knowledge_base_id,
        )
        self.assertIsNotNone(knowledge_base)
        self.assertEqual(
            knowledge_base.data.chunker_config,
            ChunkerConfig(
                type="alternate_approx_token",
                parameters={},
            ),
        )
        self.assertEqual(
            sorted((document.id, document.status) for document in documents),
            sorted(
                [
                    ("doc-ready", "pending"),
                    (uploaded.id, "pending"),
                ],
            ),
        )

    async def test_unchanged_chunker_does_not_reindex(self) -> None:
        """Equivalent configuration updates leave document state intact."""
        await self._store_document("doc-ready", "ready")

        await self._service.update_knowledge_base(
            user_id="user-1",
            knowledge_base_id=self._knowledge_base_id,
            name="renamed",
            chunker_config=ChunkerConfig(
                type="approx_token",
                parameters={"chunk_size": 512, "overlap": 50},
            ),
        )
        document = await self._storage.get_knowledge_document(
            "user-1",
            self._knowledge_base_id,
            "doc-ready",
        )
        queued = await self._bus.queue_drain(
            MessageBusKeys.index_tasks_queue(),
        )

        self.assertIsNotNone(document)
        self.assertEqual(
            (
                document.status,
                document.data.error,
                document.data.chunk_count,
            ),
            ("ready", "old error", 3),
        )
        self.assertEqual(queued, [])

    async def test_reindex_grace_and_duplicate_consumption(self) -> None:
        """Fresh reindexes wait for their grace and index once per batch."""
        await self._store_document("doc-ready", "ready")
        document = await self._storage.get_knowledge_document(
            "user-1",
            self._knowledge_base_id,
            "doc-ready",
        )
        created_at = document.created_at
        document.data.blob_uri = await self._service._blob_store.write_stream(
            key="reindex/document.txt",
            stream=BytesIO(b"abcdefgh01234567" * 16),
        )
        await self._storage.upsert_knowledge_document("user-1", document)
        worker = IndexWorker(
            message_bus=self._bus,
            storage=self._storage,
            blob_store=self._service._blob_store,
            knowledge_base_manager=self._manager,
            parsers=[TextParser()],
            node_id="worker",
        )
        vector_store = self._manager._vector_store
        with patch.object(
            vector_store,
            "insert",
            wraps=vector_store.insert,
        ) as insert:
            for batch, chunk_size in enumerate((4, 8), start=1):
                with self.subTest(batch=batch):
                    await self._service.update_knowledge_base(
                        "user-1",
                        self._knowledge_base_id,
                        chunker_config=ChunkerConfig(
                            type="approx_token",
                            parameters={
                                "chunk_size": chunk_size,
                                "overlap": 0,
                            },
                        ),
                    )
                    await IndexSweeper(self._storage, self._bus)._sweep_once()
                    await self._assert_dispatched(["doc-ready"])
                    for _ in range(2):
                        await worker.process(
                            "user-1",
                            self._knowledge_base_id,
                            "doc-ready",
                        )
                    stored = await self._storage.get_knowledge_document(
                        "user-1",
                        self._knowledge_base_id,
                        "doc-ready",
                    )
                    self.assertEqual(
                        (
                            insert.await_count,
                            stored.created_at,
                            stored.status,
                            stored.data.chunk_count,
                        ),
                        (batch, created_at, "ready", 64 // chunk_size),
                    )

    @asynccontextmanager
    async def _paused_reindex(
        self,
        phase: str,
        chunk_size: int = 64,
    ) -> AsyncIterator[KnowledgeDocumentRecord]:
        """Pause a real reindex at parsing or vector insertion."""
        await self._store_document("doc-race", "ready")
        document = await self._storage.get_knowledge_document(
            "user-1",
            self._knowledge_base_id,
            "doc-race",
        )
        document.data.blob_uri = await self._service._blob_store.write_stream(
            key="race.txt",
            stream=BytesIO(b"hello world"),
        )
        await self._storage.upsert_knowledge_document("user-1", document)
        await self._service.update_knowledge_base(
            "user-1",
            self._knowledge_base_id,
            chunker_config=ChunkerConfig(
                type="approx_token",
                parameters={"chunk_size": chunk_size, "overlap": 0},
            ),
        )
        worker = IndexWorker(
            storage=self._storage,
            blob_store=self._service._blob_store,
            knowledge_base_manager=self._manager,
            message_bus=self._bus,
            parsers=[TextParser()],
            node_id="worker",
        )
        target = worker if phase == "_parse" else self._manager._vector_store
        original = getattr(target, phase)
        reached, proceed = asyncio.Event(), asyncio.Event()

        async def paused(*args: Any, **kwargs: Any) -> Any:
            """Signal the barrier before running the original operation."""
            reached.set()
            await proceed.wait()
            return await original(*args, **kwargs)

        with patch.object(target, phase, side_effect=paused):
            task = asyncio.create_task(
                worker.process(
                    "user-1",
                    self._knowledge_base_id,
                    document.id,
                ),
            )
            try:
                await asyncio.wait_for(reached.wait(), timeout=5)
                yield document
            finally:
                proceed.set()
                await asyncio.wait_for(task, timeout=5)

    async def _assert_race_document_deleted(
        self,
        document: KnowledgeDocumentRecord,
    ) -> None:
        """Require deletion of the record, every vector, and the blob."""
        record = await self._storage.get_knowledge_document(
            "user-1",
            self._knowledge_base_id,
            document.id,
        )
        chunks = await self._manager._vector_store.list_chunks(
            "kb_collection",
            document.id,
        )
        exists = await self._service._blob_store.exists(
            document.data.blob_uri,
        )
        self.assertEqual((record, chunks, exists), (None, [], False))

    async def test_deletion_before_final_write_skips_vectors(self) -> None:
        """A removed document or KB cannot be resurrected after parsing."""
        vectors = self._manager._vector_store
        for delete_kb in (False, True):
            with self.subTest(delete_kb=delete_kb):
                with patch.object(
                    vectors,
                    "insert",
                    wraps=vectors.insert,
                ) as put:
                    async with self._paused_reindex(
                        "_parse",
                        32 if delete_kb else 64,
                    ) as document:
                        if delete_kb:
                            await self._service.delete_knowledge_base(
                                "user-1",
                                self._knowledge_base_id,
                            )
                        else:
                            await self._service.delete_document(
                                "user-1",
                                self._knowledge_base_id,
                                document.id,
                            )
                    self.assertEqual(put.await_count, 0)
                await self._assert_race_document_deleted(document)

    async def test_deletion_waits_for_final_write(self) -> None:
        """Deletion waits for an active insert, then removes its result."""
        attempted = asyncio.Event()
        acquire_lock = self._bus.acquire_lock

        @asynccontextmanager
        async def observed_lock(
            key: str,
            **kwargs: Any,
        ) -> AsyncIterator[None]:
            """Observe deletion trying to acquire the shared lock."""
            attempted.set()
            async with acquire_lock(key, **kwargs):
                yield

        for delete_kb in (False, True):
            with self.subTest(delete_kb=delete_kb):
                attempted.clear()
                async with self._paused_reindex(
                    "insert",
                    32 if delete_kb else 64,
                ) as document:
                    with patch.object(
                        self._bus,
                        "acquire_lock",
                        side_effect=observed_lock,
                    ):
                        deletion = asyncio.create_task(
                            self._service.delete_knowledge_base(
                                "user-1",
                                self._knowledge_base_id,
                            )
                            if delete_kb
                            else self._service.delete_document(
                                "user-1",
                                self._knowledge_base_id,
                                document.id,
                            ),
                        )
                        await asyncio.wait_for(attempted.wait(), timeout=5)
                        self.assertEqual(deletion.done(), False)
                await asyncio.wait_for(deletion, timeout=5)
                await self._assert_race_document_deleted(document)

    async def test_lost_lease_before_final_write_skips_vectors(self) -> None:
        """Do not wait for the periodic heartbeat to reject a stale worker."""
        vectors = self._manager._vector_store
        with patch.object(vectors, "insert", wraps=vectors.insert) as put:
            async with self._paused_reindex("_parse") as document:
                active = await self._storage.get_knowledge_document(
                    "user-1",
                    self._knowledge_base_id,
                    document.id,
                )
                await self._storage.release_knowledge_document_lease(
                    "user-1",
                    self._knowledge_base_id,
                    document.id,
                    active.processing_node,
                )
                self.assertEqual(
                    await self._storage.acquire_knowledge_document_lease(
                        "user-1",
                        self._knowledge_base_id,
                        document.id,
                        "replacement",
                        timedelta(minutes=5),
                    ),
                    True,
                )
            stored = await self._storage.get_knowledge_document(
                "user-1",
                self._knowledge_base_id,
                document.id,
            )
            self.assertEqual(
                (put.await_count, stored.status, stored.processing_node),
                (0, "parsing", "replacement"),
            )

    async def test_old_cleanup_preserves_new_lease_on_same_worker(
        self,
    ) -> None:
        """A ready task's delayed cleanup cannot release a new reindex."""
        await self._store_document("doc-lease", "pending")
        document = await self._storage.get_knowledge_document(
            "user-1",
            self._knowledge_base_id,
            "doc-lease",
        )
        document.data.blob_uri = await self._service._blob_store.write_stream(
            key="lease.txt",
            stream=BytesIO(b"hello world"),
        )
        await self._storage.upsert_knowledge_document("user-1", document)
        worker = IndexWorker(
            storage=self._storage,
            blob_store=self._service._blob_store,
            knowledge_base_manager=self._manager,
            message_bus=self._bus,
            parsers=[TextParser()],
            node_id="same-worker",
        )
        worker._renew_interval = timedelta(milliseconds=10)
        cleanup_entered, allow_cleanup = asyncio.Event(), asyncio.Event()
        parsing_entered, allow_parsing = asyncio.Event(), asyncio.Event()
        cleanup_finished, renewed = asyncio.Event(), asyncio.Event()
        release = self._storage.release_knowledge_document_lease
        renew = self._storage.renew_knowledge_document_lease
        parse = worker._parse
        releases: list[str] = []
        renewals: list[bool] = []
        tasks: list[asyncio.Task[None]] = []

        async def delayed_release(**kwargs: Any) -> None:
            """Keep the first task alive after it has marked ready."""
            releases.append(kwargs["processing_node"])
            if len(releases) == 1:
                cleanup_entered.set()
                await allow_cleanup.wait()
            await release(**kwargs)

        async def paused_parse(*args: Any, **kwargs: Any) -> Any:
            """Keep the next task running while its heartbeat renews."""
            parsing_entered.set()
            await allow_parsing.wait()
            return await parse(*args, **kwargs)

        async def observed_renew(**kwargs: Any) -> bool:
            """Observe a real heartbeat after the old cleanup completes."""
            result = await renew(**kwargs)
            if cleanup_finished.is_set():
                renewals.append(result)
                renewed.set()
            return result

        with patch.object(
            self._storage,
            "release_knowledge_document_lease",
            side_effect=delayed_release,
        ), patch.object(
            self._storage,
            "renew_knowledge_document_lease",
            side_effect=observed_renew,
        ):
            try:
                first = asyncio.create_task(
                    worker.process(
                        "user-1",
                        self._knowledge_base_id,
                        document.id,
                    ),
                )
                tasks.append(first)
                await asyncio.wait_for(cleanup_entered.wait(), timeout=5)
                await self._service.update_knowledge_base(
                    "user-1",
                    self._knowledge_base_id,
                    chunker_config=ChunkerConfig(
                        type="approx_token",
                        parameters={"chunk_size": 64, "overlap": 0},
                    ),
                )
                with patch.object(worker, "_parse", side_effect=paused_parse):
                    second = asyncio.create_task(
                        worker.process(
                            "user-1",
                            self._knowledge_base_id,
                            document.id,
                        ),
                    )
                    tasks.append(second)
                    await asyncio.wait_for(parsing_entered.wait(), timeout=5)
                    active = await self._storage.get_knowledge_document(
                        "user-1",
                        self._knowledge_base_id,
                        document.id,
                    )
                    allow_cleanup.set()
                    await asyncio.wait_for(first, timeout=5)
                    cleanup_finished.set()
                    await asyncio.wait_for(renewed.wait(), timeout=5)
                    current = await self._storage.get_knowledge_document(
                        "user-1",
                        self._knowledge_base_id,
                        document.id,
                    )
                    self.assertEqual(
                        (current.processing_node, all(renewals)),
                        (active.processing_node, True),
                    )
                    self.assertNotEqual(releases[0], active.processing_node)
                    allow_parsing.set()
                    await asyncio.wait_for(second, timeout=5)
                final = await self._storage.get_knowledge_document(
                    "user-1",
                    self._knowledge_base_id,
                    document.id,
                )
                self.assertEqual(
                    (final.status, final.processing_node, final.data.error),
                    ("ready", None, None),
                )
                self.assertEqual(
                    releases,
                    [releases[0], active.processing_node],
                )
            finally:
                allow_cleanup.set()
                allow_parsing.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def test_lost_worker_cannot_fail_replacement_waiting_for_lock(
        self,
    ) -> None:
        """A stale heartbeat cannot make its replacement skip indexing."""
        await self._store_document("doc-takeover", "pending")
        document = await self._storage.get_knowledge_document(
            "user-1",
            self._knowledge_base_id,
            "doc-takeover",
        )
        document.data.error = None
        document.data.chunk_count = 0
        document.data.blob_uri = await self._service._blob_store.write_stream(
            key="takeover.txt",
            stream=BytesIO(b"hello world"),
        )
        await self._storage.upsert_knowledge_document("user-1", document)
        workers = [
            IndexWorker(
                storage=self._storage,
                blob_store=self._service._blob_store,
                knowledge_base_manager=self._manager,
                message_bus=self._bus,
                parsers=[TextParser()],
                node_id=node,
            )
            for node in ("old", "replacement")
        ]
        old, replacement = workers
        old._renew_interval = timedelta(milliseconds=1)
        parsing, detect_loss, waiting = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        heartbeat = old._heartbeat
        acquire = self._bus.acquire_lock
        tasks: list[asyncio.Task[None]] = []

        async def paused_parse(*args: Any, **kwargs: Any) -> Any:
            """Keep the old pipeline alive until its heartbeat cancels it."""
            del args, kwargs
            parsing.set()
            await asyncio.Event().wait()

        async def gated_heartbeat(*args: Any, **kwargs: Any) -> None:
            """Detect lease loss only after the replacement is waiting."""
            await detect_loss.wait()
            await heartbeat(*args, **kwargs)

        @asynccontextmanager
        async def observed_lock(
            *args: Any,
            **kwargs: Any,
        ) -> AsyncIterator[None]:
            """Signal that the replacement reached the final write lock."""
            waiting.set()
            async with acquire(*args, **kwargs):
                yield

        with patch.object(
            old,
            "_parse",
            side_effect=paused_parse,
        ), patch.object(
            old,
            "_heartbeat",
            side_effect=gated_heartbeat,
        ):
            try:
                first = asyncio.create_task(
                    old.process(
                        "user-1",
                        self._knowledge_base_id,
                        document.id,
                    ),
                )
                tasks.append(first)
                await asyncio.wait_for(parsing.wait(), timeout=5)
                active = await self._storage.get_knowledge_document(
                    "user-1",
                    self._knowledge_base_id,
                    document.id,
                )
                active.lease_expires_at = datetime.now() - timedelta(seconds=1)
                await self._storage.upsert_knowledge_document("user-1", active)
                async with acquire(
                    MessageBusKeys.knowledge_base_mutation_lock(
                        "user-1",
                        self._knowledge_base_id,
                    ),
                ):
                    with patch.object(
                        self._bus,
                        "acquire_lock",
                        side_effect=observed_lock,
                    ):
                        second = asyncio.create_task(
                            replacement.process(
                                "user-1",
                                self._knowledge_base_id,
                                document.id,
                            ),
                        )
                        tasks.append(second)
                        await asyncio.wait_for(waiting.wait(), timeout=5)
                        before = await self._storage.get_knowledge_document(
                            "user-1",
                            self._knowledge_base_id,
                            document.id,
                        )
                        detect_loss.set()
                        await asyncio.wait_for(first, timeout=5)
                        after = await self._storage.get_knowledge_document(
                            "user-1",
                            self._knowledge_base_id,
                            document.id,
                        )
                        self.assertEqual(after, before)
                await asyncio.wait_for(second, timeout=5)
                final = await self._storage.get_knowledge_document(
                    "user-1",
                    self._knowledge_base_id,
                    document.id,
                )
                chunks = await self._manager._vector_store.list_chunks(
                    "kb_collection",
                    final.data.vector_id(document.id),
                )
                self.assertEqual(
                    (
                        final.status,
                        final.data.error,
                        final.data.chunk_count,
                        final.processing_node,
                        len(chunks),
                    ),
                    ("ready", None, 1, None, 1),
                )
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def test_legacy_chunker_matches_worker_default(self) -> None:
        """Legacy defaults do not change with the registration order."""
        service = KnowledgeBaseService(
            storage=self._storage,
            knowledge_base_manager=self._manager,
            blob_store=LocalBlobStore(self._temporary_directory.name),
            message_bus=self._bus,
            resource_access_service=ResourceAccessService(
                self._storage,
                DenyAllResourceAccessPolicy(),
            ),
            chunkers=[_AlternateApproxTokenChunker, ApproxTokenChunker],
        )

        self.assertEqual(
            (
                service._chunker_configs_match(
                    None,
                    ChunkerConfig(type="alternate_approx_token"),
                ),
                service._chunker_configs_match(
                    None,
                    ChunkerConfig(type="approx_token"),
                ),
                service._default_chunker_config(),
            ),
            (False, True, ChunkerConfig(type="alternate_approx_token")),
        )

        record = await self._storage.get_knowledge_base(
            "user-1",
            self._knowledge_base_id,
        )
        record.data.chunker_config = None
        await self._storage.upsert_knowledge_base("user-1", record)
        await self._store_document("doc-legacy", "ready")
        await service.update_knowledge_base(
            "user-1",
            self._knowledge_base_id,
            chunker_config=ChunkerConfig(type="alternate_approx_token"),
        )
        await self._assert_dispatched(["doc-legacy"])

    async def _assert_dispatched(self, document_ids: list[str]) -> None:
        """Assert the complete dispatch payload and recovery marker."""
        queued = await self._bus.queue_drain(
            MessageBusKeys.index_tasks_queue(),
        )
        self.assertEqual(
            sorted((payload for _, payload in queued), key=str),
            sorted(
                [
                    {
                        "user_id": "user-1",
                        "knowledge_base_id": self._knowledge_base_id,
                        "document_id": document_id,
                    }
                    for document_id in document_ids
                ],
                key=str,
            ),
        )
        self.assertEqual(
            await self._storage.list_pending_knowledge_base_reindexes(),
            [],
        )

    async def test_retry_recovers_enqueue_failure(self) -> None:
        """The same PATCH resumes even after its config was persisted."""
        await self._store_document("doc-a", "ready")
        await self._store_document("doc-b", "ready")
        requested = ChunkerConfig(type="alternate_approx_token")
        with patch.object(
            self._bus,
            "queue_push",
            side_effect=RuntimeError("queue unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "queue unavailable"):
                await self._service.update_knowledge_base(
                    "user-1",
                    self._knowledge_base_id,
                    chunker_config=requested,
                )

        stored = await self._storage.get_knowledge_base(
            "user-1",
            self._knowledge_base_id,
        )
        self.assertEqual(
            (stored.data.chunker_config, stored.data.reindex_pending),
            (requested, True),
        )
        await self._service.update_knowledge_base(
            "user-1",
            self._knowledge_base_id,
            chunker_config=requested,
        )
        await self._assert_dispatched(["doc-a", "doc-b"])
        documents = await self._storage.list_knowledge_documents(
            "user-1",
            self._knowledge_base_id,
        )
        self.assertEqual(
            sorted(
                (doc.id, doc.status, doc.data.reindex_id) for doc in documents
            ),
            [
                ("doc-a", "pending", stored.data.reindex_id),
                ("doc-b", "pending", stored.data.reindex_id),
            ],
        )

    async def test_sweeper_recovers_write_failure_without_repeating_work(
        self,
    ) -> None:
        """A completed document survives recovery of a partial batch."""
        await self._store_document("doc-a", "ready")
        await self._store_document("doc-b", "ready")
        original_upsert = self._storage.upsert_knowledge_document
        writes = 0

        async def fail_second_write(
            user_id: str,
            record: KnowledgeDocumentRecord,
        ) -> KnowledgeDocumentRecord:
            nonlocal writes
            writes += 1
            if writes == 2:
                raise RuntimeError("storage unavailable")
            return await original_upsert(user_id, record)

        with patch.object(
            self._storage,
            "upsert_knowledge_document",
            new=fail_second_write,
        ):
            with self.assertRaisesRegex(RuntimeError, "storage unavailable"):
                await self._service.update_knowledge_base(
                    "user-1",
                    self._knowledge_base_id,
                    chunker_config=ChunkerConfig(
                        type="alternate_approx_token",
                    ),
                )

        queued = await self._bus.queue_drain(
            MessageBusKeys.index_tasks_queue(),
        )
        self.assertEqual(len(queued), 1)
        completed_id = queued[0][1]["document_id"]
        remaining_id = ({"doc-a", "doc-b"} - {completed_id}).pop()
        await self._storage.update_knowledge_document_status(
            "user-1",
            self._knowledge_base_id,
            completed_id,
            "ready",
            chunk_count=9,
        )
        completed = await self._storage.get_knowledge_document(
            "user-1",
            self._knowledge_base_id,
            completed_id,
        )
        sweeper = IndexSweeper(self._storage, self._bus)
        await sweeper._sweep_once()
        await self._assert_dispatched([remaining_id])
        self.assertEqual(
            await self._storage.get_knowledge_document(
                "user-1",
                self._knowledge_base_id,
                completed_id,
            ),
            completed,
        )

    async def test_delete_waits_for_reindex_and_is_not_resurrected(
        self,
    ) -> None:
        """Deleting a document cannot interleave with batch resets."""
        await self._store_document("doc-delete", "ready")
        entered = asyncio.Event()
        release = asyncio.Event()
        original = self._manager.update_knowledge_base

        async def blocking_update(**kwargs: Any) -> KnowledgeBaseRecord:
            entered.set()
            await release.wait()
            return await original(**kwargs)

        with patch.object(
            self._manager,
            "update_knowledge_base",
            new=blocking_update,
        ):
            update = asyncio.create_task(
                self._service.update_knowledge_base(
                    "user-1",
                    self._knowledge_base_id,
                    chunker_config=ChunkerConfig(
                        type="alternate_approx_token",
                    ),
                ),
            )
            await asyncio.wait_for(entered.wait(), 1)
            deletion = asyncio.create_task(
                self._service.delete_document(
                    "user-1",
                    self._knowledge_base_id,
                    "doc-delete",
                ),
            )
            await asyncio.sleep(0)
            self.assertFalse(deletion.done())
            release.set()
            await asyncio.wait_for(asyncio.gather(update, deletion), 1)

        await IndexSweeper(self._storage, self._bus)._sweep_once()
        self.assertEqual(
            await self._storage.list_knowledge_documents(
                "user-1",
                self._knowledge_base_id,
            ),
            [],
        )

    async def test_deleted_document_is_skipped_by_recovery(self) -> None:
        """Recovery reads live documents, not a stored stale snapshot."""
        await self._store_document("doc-delete", "ready")
        with patch.object(
            self._bus,
            "queue_push",
            side_effect=RuntimeError("queue unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                await self._service.update_knowledge_base(
                    "user-1",
                    self._knowledge_base_id,
                    chunker_config=ChunkerConfig(
                        type="alternate_approx_token",
                    ),
                )
        await self._service.delete_document(
            "user-1",
            self._knowledge_base_id,
            "doc-delete",
        )
        await IndexSweeper(self._storage, self._bus)._sweep_once()
        await self._assert_dispatched([])
        self.assertEqual(
            await self._storage.list_knowledge_documents(
                "user-1",
                self._knowledge_base_id,
            ),
            [],
        )

    async def test_completed_batch_recovers_failed_final_commit(self) -> None:
        """A lost completion write does not redo already indexed work."""
        await self._store_document("doc-ready", "ready")
        original_upsert = self._storage.upsert_knowledge_base

        async def fail_completion(
            user_id: str,
            record: KnowledgeBaseRecord,
        ) -> KnowledgeBaseRecord:
            if not record.data.reindex_pending:
                raise RuntimeError("completion write failed")
            return await original_upsert(user_id, record)

        with patch.object(
            self._storage,
            "upsert_knowledge_base",
            new=fail_completion,
        ):
            with self.assertRaisesRegex(RuntimeError, "completion write"):
                await self._service.update_knowledge_base(
                    "user-1",
                    self._knowledge_base_id,
                    chunker_config=ChunkerConfig(
                        type="alternate_approx_token",
                    ),
                )
        await self._bus.queue_drain(MessageBusKeys.index_tasks_queue())
        await self._storage.update_knowledge_document_status(
            "user-1",
            self._knowledge_base_id,
            "doc-ready",
            "ready",
            chunk_count=7,
        )
        completed = await self._storage.get_knowledge_document(
            "user-1",
            self._knowledge_base_id,
            "doc-ready",
        )
        await IndexSweeper(self._storage, self._bus)._sweep_once()
        await self._assert_dispatched([])
        self.assertEqual(
            await self._storage.get_knowledge_document(
                "user-1",
                self._knowledge_base_id,
                "doc-ready",
            ),
            completed,
        )

    async def test_deleted_knowledge_base_is_not_recovered(self) -> None:
        """A sweeper's stale scan cannot recreate a deleted KB."""
        await self._store_document("doc-ready", "ready")
        with patch.object(
            self._bus,
            "queue_push",
            side_effect=RuntimeError("queue unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                await self._service.update_knowledge_base(
                    "user-1",
                    self._knowledge_base_id,
                    chunker_config=ChunkerConfig(
                        type="alternate_approx_token",
                    ),
                )
        scanned = await self._storage.list_pending_knowledge_base_reindexes()
        self.assertEqual(len(scanned), 1)
        await self._service.delete_knowledge_base(
            "user-1",
            self._knowledge_base_id,
        )
        with patch.object(
            self._storage,
            "list_pending_knowledge_base_reindexes",
            return_value=scanned,
        ):
            await IndexSweeper(self._storage, self._bus)._sweep_once()
        await self._assert_dispatched([])
        self.assertEqual(
            await self._storage.get_knowledge_base(
                "user-1",
                self._knowledge_base_id,
            ),
            None,
        )

    async def test_upload_rechecks_deleted_knowledge_base(self) -> None:
        """An upload waiting for the lock cannot create orphan records."""
        original_write = self._service._blob_store.write_stream
        blob_uris: list[str] = []

        async def delete_after_blob_write(**kwargs: Any) -> str:
            blob_uri = await original_write(**kwargs)
            blob_uris.append(blob_uri)
            await self._service.delete_knowledge_base(
                "user-1",
                self._knowledge_base_id,
            )
            return blob_uri

        with patch.object(
            self._service._blob_store,
            "write_stream",
            new=delete_after_blob_write,
        ):
            with self.assertRaises(HTTPException) as raised:
                await self._service.register_document(
                    "user-1",
                    self._knowledge_base_id,
                    "upload.txt",
                    BytesIO(b"content"),
                    7,
                    "text/plain",
                )
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(
            await self._storage.list_knowledge_documents(
                "user-1",
                self._knowledge_base_id,
            ),
            [],
        )
        self.assertEqual(
            await self._service._blob_store.exists(blob_uris[0]),
            False,
        )

    async def test_cancelled_upload_waiting_for_kb_lock_cleans_blob(
        self,
    ) -> None:
        """Lock contention must preserve main's cancellation cleanup."""
        blob_written = asyncio.Event()
        blob_uris: list[str] = []
        original_write = self._service._blob_store.write_stream

        async def track_blob_write(**kwargs: Any) -> str:
            blob_uri = await original_write(**kwargs)
            blob_uris.append(blob_uri)
            blob_written.set()
            return blob_uri

        async with self._bus.acquire_lock(
            MessageBusKeys.knowledge_base_mutation_lock(
                "user-1",
                self._knowledge_base_id,
            ),
        ):
            with patch.object(
                self._service._blob_store,
                "write_stream",
                new=track_blob_write,
            ):
                upload = asyncio.create_task(
                    self._service.register_document(
                        "user-1",
                        self._knowledge_base_id,
                        "cancel.txt",
                        BytesIO(b"content"),
                        7,
                        "text/plain",
                    ),
                )
                try:
                    await asyncio.wait_for(blob_written.wait(), 1)
                    self.assertFalse(upload.done())
                    upload.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(upload, 1)
                finally:
                    upload.cancel()
                    await asyncio.gather(upload, return_exceptions=True)

        self.assertEqual(
            (
                await self._storage.list_knowledge_documents(
                    "user-1",
                    self._knowledge_base_id,
                ),
                await self._service._blob_store.exists(blob_uris[0]),
                await self._bus.queue_drain(
                    MessageBusKeys.index_tasks_queue(),
                ),
            ),
            ([], False, []),
        )

    async def test_in_flight_document_rejects_changed_chunker(self) -> None:
        """A concurrent index pass blocks configuration replacement."""
        await self._store_document("doc-pending", "pending")
        requested = ChunkerConfig(
            type="approx_token",
            parameters={"chunk_size": 64, "overlap": 8},
        )

        with self.assertRaises(HTTPException) as raised:
            await self._service.update_knowledge_base(
                user_id="user-1",
                knowledge_base_id=self._knowledge_base_id,
                chunker_config=requested,
            )

        stored = await self._storage.get_knowledge_base(
            "user-1",
            self._knowledge_base_id,
        )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.data.chunker_config, self._original_chunker)

    async def test_invalid_chunker_is_rejected_before_persistence(
        self,
    ) -> None:
        """Parameter validation remains authoritative on update."""
        for parameters in ({"chunk_size": 8, "overlap": 8}, {"chunk_szie": 8}):
            with self.subTest(parameters=parameters):
                with self.assertRaises(HTTPException) as raised:
                    await self._service.update_knowledge_base(
                        user_id="user-1",
                        knowledge_base_id=self._knowledge_base_id,
                        chunker_config=ChunkerConfig(
                            type="approx_token",
                            parameters=parameters,
                        ),
                    )
                self.assertEqual(raised.exception.status_code, 422)

        stored = await self._storage.get_knowledge_base(
            "user-1",
            self._knowledge_base_id,
        )
        self.assertIsNotNone(stored)
        self.assertEqual(stored.data.chunker_config, self._original_chunker)
