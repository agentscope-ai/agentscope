# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for knowledge-base chunker configuration updates."""
import asyncio
import tempfile
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

    async def test_default_chunker_follows_configured_order(self) -> None:
        """Legacy records use the same default as the create path."""
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

        matches = service._chunker_configs_match(
            None,
            ChunkerConfig(
                type="alternate_approx_token",
                parameters={},
            ),
        )

        self.assertEqual(matches, True)

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
