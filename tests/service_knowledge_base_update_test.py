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
from agentscope.app.storage import (
    ChunkerConfig,
    EmbeddingModelConfig,
    KnowledgeBaseData,
    KnowledgeBaseRecord,
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
)
from agentscope.rag import ApproxTokenChunker, TextParser


class _AlternateApproxTokenChunker(ApproxTokenChunker):
    """Second registered type used to verify chunker replacement."""

    chunker_type = "alternate_approx_token"


class KnowledgeBaseChunkerUpdateTest(IsolatedAsyncioTestCase):
    """Verify configuration persistence and reindex dispatch semantics."""

    # pylint: disable=too-many-public-methods

    async def asyncSetUp(self) -> None:
        """Build the service with in-memory storage and message bus fakes."""
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
                ("doc-error", "pending", None, None, None, 0),
                ("doc-ready", "pending", None, None, None, 0),
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
                    document.id,
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
        requested = ChunkerConfig(
            type="approx_token",
            parameters={"chunk_size": 8, "overlap": 8},
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
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.data.chunker_config, self._original_chunker)
