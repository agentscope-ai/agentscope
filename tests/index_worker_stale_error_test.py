# -*- coding: utf-8 -*-
"""Regression test for clearing stale indexing errors on retry."""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, create_autospec, patch

from agentscope.app._router._schema._knowledge_base import (
    KnowledgeDocumentView,
)
from agentscope.app._service._index_worker import IndexWorker
from agentscope.app.storage import (
    KnowledgeDocumentData,
    KnowledgeDocumentRecord,
)
from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse
from agentscope.rag import KnowledgeBase, TextParser
from agentscope.rag._vdb._vector_store import VectorStoreBase


class _Storage:
    """Mirror the status-update semantics of both storage backends."""

    def __init__(self) -> None:
        self.record = KnowledgeDocumentRecord(
            id="doc-1",
            user_id="owner",
            knowledge_base_id="kb-1",
            status="error",
            data=KnowledgeDocumentData(
                filename="source.txt",
                blob_uri="local://kb/kb-1/doc-1",
                size=13,
                content_type="text/plain",
                error="RuntimeError: embedding unavailable",
            ),
        )
        self.get_knowledge_document = AsyncMock(return_value=self.record)
        self.acquire_knowledge_document_lease = AsyncMock(return_value=True)
        self.renew_knowledge_document_lease = AsyncMock(return_value=True)
        self.release_knowledge_document_lease = AsyncMock()

    async def update_knowledge_document_status(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        status: str,
        error: str | None = None,
        chunk_count: int | None = None,
    ) -> None:
        """Update only provided fields, as the real backends do."""
        del user_id, knowledge_base_id, document_id
        self.record.status = status
        if error is not None:
            self.record.data.error = error
        if chunk_count is not None:
            self.record.data.chunk_count = chunk_count


class StaleIndexingErrorTest(IsolatedAsyncioTestCase):
    """A successful retry must leave no failure text on a ready doc."""

    async def test_retry_clears_previous_error(self) -> None:
        """The UI-visible error is cleared when indexing succeeds."""
        storage = _Storage()
        embedding = AsyncMock(spec=EmbeddingModelBase)
        embedding.dimensions = 3
        embedding.supports_multimodal = False
        embedding.return_value = EmbeddingResponse(
            embeddings=[[1.0, 0.0, 0.0]],
        )
        vectors = create_autospec(VectorStoreBase, instance=True)
        vectors.has_collection.return_value = True
        knowledge = KnowledgeBase(
            name="kb-1",
            description="Test knowledge base",
            embedding_model=embedding,
            vector_store=vectors,
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
        worker = IndexWorker(
            storage=storage,
            blob_store=object(),
            knowledge_base_manager=manager,
            parsers=[TextParser()],
            node_id="test-node",
        )
        with patch.object(
            worker,
            "_read_blob",
            new_callable=AsyncMock,
            return_value=b"stable source",
        ):
            await worker.process("owner", "kb-1", "doc-1")

        self.assertEqual(storage.record.status, "ready")
        view = KnowledgeDocumentView.from_record(storage.record)
        self.assertIsNone(view.error)
