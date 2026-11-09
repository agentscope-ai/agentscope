# -*- coding: utf-8 -*-
"""Regression tests for duplicate index tasks and explicit retries."""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, create_autospec, patch

from agentscope.app._service._index_worker import IndexWorker
from agentscope.app.storage import (
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
    KnowledgeDocumentStatus,
)
from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse
from agentscope.rag import KnowledgeBase, TextParser
from agentscope.rag._vdb._vector_store import VectorStoreBase


class _StubStorage:
    """Keep document state while mocking lease operations."""

    def __init__(self) -> None:
        self.record = KnowledgeDocumentRecord(
            id="doc-1",
            user_id="owner",
            knowledge_base_id="kb-1",
            data=KnowledgeDocumentData(
                filename="source.txt",
                blob_uri="local://kb/kb-1/doc-1",
                size=13,
                content_type="text/plain",
            ),
        )
        self.status_writes: list[str] = []
        self.get_knowledge_document = AsyncMock(return_value=self.record)
        self.acquire_knowledge_document_lease = AsyncMock(return_value=True)
        self.renew_knowledge_document_lease = AsyncMock(return_value=True)
        self.release_knowledge_document_lease = AsyncMock()

    async def update_knowledge_document_status(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        status: KnowledgeDocumentStatus,
        error: str | None = None,
        chunk_count: int | None = None,
    ) -> None:
        """Apply status updates with the storage backends' semantics."""
        del user_id, knowledge_base_id, document_id
        self.status_writes.append(status)
        self.record.status = status
        if error is not None:
            self.record.data.error = error
        if chunk_count is not None:
            self.record.data.chunk_count = chunk_count


class IndexWorkerDuplicateTaskTest(IsolatedAsyncioTestCase):
    """Completed documents survive redelivery; retries still run."""

    def setUp(self) -> None:
        self.storage = _StubStorage()
        self.embedding = AsyncMock(spec=EmbeddingModelBase)
        self.embedding.dimensions = 3
        self.embedding.supports_multimodal = False
        self.embedding.return_value = EmbeddingResponse(
            embeddings=[[1.0, 0.0, 0.0]],
        )
        self.vectors = create_autospec(VectorStoreBase, instance=True)
        self.vectors.has_collection.return_value = True
        knowledge = KnowledgeBase(
            name="kb-1",
            description="Test knowledge base",
            embedding_model=self.embedding,
            vector_store=self.vectors,
            collection="kb-1",
        )
        manager = SimpleNamespace(
            get_knowledge_base=AsyncMock(
                return_value=SimpleNamespace(
                    id="kb-1",
                    data=SimpleNamespace(chunker_config=None),
                ),
            ),
            get_knowledge=AsyncMock(return_value=knowledge),
        )
        self.worker = IndexWorker(
            storage=self.storage,
            blob_store=object(),
            knowledge_base_manager=manager,
            parsers=[TextParser()],
            node_id="test-node",
        )
        blob_patch = patch.object(
            self.worker,
            "_read_blob",
            new_callable=AsyncMock,
            return_value=b"stable source",
        )
        self.blob_read = blob_patch.start()
        self.addCleanup(blob_patch.stop)

    def _snapshot(self) -> dict[str, object]:
        """Capture document state and all indexing side effects."""
        return {
            "record": self.storage.record.model_dump(),
            "statuses": list(self.storage.status_writes),
            "embeddings": list(self.embedding.await_args_list),
            "vectors": list(self.vectors.mock_calls),
            "blob_reads": list(self.blob_read.await_args_list),
        }

    async def test_duplicate_after_ready_leaves_document_untouched(
        self,
    ) -> None:
        """A late duplicate preserves the completed document and vectors."""
        await self.worker.process("owner", "kb-1", "doc-1")
        self.assertEqual(self.storage.record.status, "ready")
        before = self._snapshot()

        await self.worker.process("owner", "kb-1", "doc-1")

        self.assertEqual(self._snapshot(), before)

    async def test_duplicate_after_ready_releases_its_lease(self) -> None:
        """Skipping a ready document still releases its acquired lease."""
        self.storage.record.status = "ready"

        await self.worker.process("owner", "kb-1", "doc-1")

        self.storage.acquire_knowledge_document_lease.assert_awaited_once()
        self.storage.release_knowledge_document_lease.assert_awaited_once_with(
            user_id="owner",
            knowledge_base_id="kb-1",
            document_id="doc-1",
            processing_node="test-node",
        )

    async def test_duplicate_after_ready_survives_a_failing_rerun(
        self,
    ) -> None:
        """An unavailable embedding backend cannot damage a ready document."""
        await self.worker.process("owner", "kb-1", "doc-1")
        self.assertEqual(self.storage.record.status, "ready")
        before = self._snapshot()
        self.embedding.side_effect = RuntimeError("embedding unavailable")

        await self.worker.process("owner", "kb-1", "doc-1")

        self.assertEqual(self._snapshot(), before)

    async def test_pending_document_is_still_indexed(self) -> None:
        """A pending document completes its first indexing run."""
        await self.worker.process("owner", "kb-1", "doc-1")

        self.assertEqual(
            (
                self.storage.record.status,
                self.storage.record.data.chunk_count,
                self.storage.status_writes,
                self.embedding.await_count,
                self.vectors.delete.await_count,
                self.vectors.insert.await_count,
            ),
            (
                "ready",
                1,
                ["parsing", "chunking", "indexing", "ready"],
                1,
                1,
                1,
            ),
        )

    async def test_error_document_is_reindexed(self) -> None:
        """A failed document can be indexed on a later delivery."""
        self.embedding.side_effect = RuntimeError("embedding unavailable")
        await self.worker.process("owner", "kb-1", "doc-1")
        self.assertEqual(self.storage.record.status, "error")
        self.embedding.side_effect = None

        await self.worker.process("owner", "kb-1", "doc-1")

        self.assertEqual(
            (
                self.storage.record.status,
                self.storage.record.data.chunk_count,
                self.embedding.await_count,
                self.vectors.delete.await_count,
                self.vectors.insert.await_count,
            ),
            ("ready", 1, 2, 2, 1),
        )

    async def test_ready_document_is_reindexed_after_status_reset(
        self,
    ) -> None:
        """Resetting a ready document to pending permits reindexing."""
        await self.worker.process("owner", "kb-1", "doc-1")
        self.assertEqual(self.storage.record.status, "ready")
        await self.storage.update_knowledge_document_status(
            "owner",
            "kb-1",
            "doc-1",
            "pending",
        )

        await self.worker.process("owner", "kb-1", "doc-1")

        self.assertEqual(
            (
                self.storage.record.status,
                self.storage.record.data.chunk_count,
                self.embedding.await_count,
                self.vectors.delete.await_count,
                self.vectors.insert.await_count,
            ),
            ("ready", 1, 2, 2, 2),
        )
