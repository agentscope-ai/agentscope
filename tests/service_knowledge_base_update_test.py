# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for knowledge-base chunker configuration updates."""
import asyncio
import tempfile
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any
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
from agentscope.rag import ApproxTokenChunker


class _AlternateApproxTokenChunker(ApproxTokenChunker):
    """Second registered type used to verify chunker replacement."""

    chunker_type = "alternate_approx_token"


class KnowledgeBaseChunkerUpdateTest(IsolatedAsyncioTestCase):
    """Verify configuration persistence and reindex dispatch semantics."""

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
