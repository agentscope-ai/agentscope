# -*- coding: utf-8 -*-
"""Regression tests for :class:`IndexWorker` duplicate-task admission.

The durable index queue permits duplicate entries, and the processing
lease only excludes workers that are indexing *right now*.  A delivery
that arrives after the first run reached ``ready`` therefore used to run
the whole pipeline again: it deleted the document's existing vectors
before re-inserting them, so a transient failure on that rerun left a
healthy document in ``error`` with no chunks at all.
"""
import io
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.app._service._index_worker import IndexWorker
from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse
from agentscope.rag import KnowledgeBase, TextParser
from agentscope.rag._vdb._vector_store import VectorRecord, VectorStoreBase


class _RecordingVectorStore(VectorStoreBase):
    """Vector store double recording what the pipeline does to it."""

    def __init__(self) -> None:
        super().__init__()
        self.dimensions = 3
        self.deleted: list[str] = []
        self.inserted: list[str] = []

    async def create_collection(self, name: str, dimensions: int) -> None:
        """Accept collection creation as a no-op."""
        del name, dimensions

    async def has_collection(self, name: str) -> bool:
        """Pretend the collection always exists."""
        del name
        return True

    async def insert(
        self,
        collection: str,
        records: list[VectorRecord],
    ) -> None:
        """Record one insert per incoming record."""
        del collection
        self.inserted.extend(r.document_id for r in records)

    async def delete(
        self,
        collection: str,
        document_id: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> None:
        """Record that the document's vectors were dropped."""
        del collection, metadata_filter
        self.deleted.append(document_id)

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Never queried by these tests."""
        del collection, query_vector, top_k, metadata_filter
        return []

    async def delete_collection(self, name: str) -> None:
        """Never called by these tests."""
        del name

    async def list_documents(
        self,
        collection: str,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Never queried by these tests."""
        del collection, metadata_filter
        return []


class _StubStorage:
    """Storage double holding one document record and its lease."""

    def __init__(self, status: str) -> None:
        self.record = SimpleNamespace(
            id="doc-1",
            user_id="owner",
            knowledge_base_id="kb-1",
            status=status,
            data=SimpleNamespace(
                filename="source.txt",
                blob_uri="local://kb/kb-1/doc-1",
                size=13,
                content_type="text/plain",
                error=None,
                chunk_count=0,
            ),
        )
        self.held_by: str | None = None
        self.status_writes: list[str] = []
        self.lease_acquisitions = 0

    async def acquire_knowledge_document_lease(self, **kwargs: Any) -> bool:
        """Grant the lease unless another node still holds it."""
        self.lease_acquisitions += 1
        node = kwargs["processing_node"]
        if self.held_by not in (None, node):
            return False
        self.held_by = node
        return True

    async def renew_knowledge_document_lease(self, **kwargs: Any) -> bool:
        """Keep the lease alive for the whole test."""
        del kwargs
        return True

    async def release_knowledge_document_lease(self, **kwargs: Any) -> None:
        """Free the lease so a later delivery can take it."""
        del kwargs
        self.held_by = None

    async def get_knowledge_document(self, *args: Any) -> Any:
        """Return the single staged record."""
        del args
        return self.record

    async def update_knowledge_document_status(
        self,
        user_id: str,
        knowledge_base_id: str,
        document_id: str,
        status: str,
        error: str | None = None,
        chunk_count: int | None = None,
    ) -> None:
        """Record every status transition the pipeline performs."""
        del user_id, knowledge_base_id, document_id
        self.status_writes.append(status)
        self.record.status = status
        self.record.data.error = error
        if chunk_count is not None:
            self.record.data.chunk_count = chunk_count


class _StubBlobStore:
    """Blob store double serving one fixed payload."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def open(self, uri: str) -> Any:
        """Return an async context handle over the payload."""
        del uri
        handle = io.BytesIO(self._payload)

        class _Handle:
            """Async read handle matching ``BlobStoreBase.open``."""

            async def read(self, size: int = -1) -> bytes:
                """Read up to ``size`` bytes."""
                return handle.read(size)

            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *exc: Any) -> None:
                handle.close()

        return _Handle()


class IndexWorkerDuplicateTaskTest(IsolatedAsyncioTestCase):
    """A redelivered task must not disturb a completed document."""

    def _build(self, status: str) -> tuple[IndexWorker, Any, Any, Any]:
        """Build a worker over stubbed storage, blobs and vectors."""
        storage = _StubStorage(status)
        embedding = AsyncMock(spec=EmbeddingModelBase)
        embedding.dimensions = 3
        embedding.supports_multimodal = False
        embedding.return_value = EmbeddingResponse(
            embeddings=[[1.0, 0.0, 0.0]],
        )
        vector_store = _RecordingVectorStore()
        knowledge = KnowledgeBase(
            name="kb-1",
            description="Test knowledge base",
            embedding_model=embedding,
            vector_store=vector_store,
            collection="kb-1",
        )
        manager = SimpleNamespace(
            get_knowledge_base=AsyncMock(
                return_value=SimpleNamespace(
                    id="kb-1",
                    user_id="owner",
                    data=SimpleNamespace(chunker_config=None),
                ),
            ),
            get_knowledge=AsyncMock(return_value=knowledge),
        )
        worker = IndexWorker(
            storage=storage,
            blob_store=_StubBlobStore(b"stable source"),
            knowledge_base_manager=manager,
            parsers=[TextParser()],
            node_id="test-node",
            lease_ttl=timedelta(seconds=90),
        )
        return worker, storage, embedding, vector_store

    async def test_duplicate_after_ready_leaves_document_untouched(
        self,
    ) -> None:
        """A delivery after ``ready`` neither rewrites vectors nor status."""
        worker, storage, embedding, vectors = self._build("pending")

        await worker.process("owner", "kb-1", "doc-1")
        first_snapshot = {
            "status": storage.record.status,
            "chunk_count": storage.record.data.chunk_count,
            "status_writes": list(storage.status_writes),
            "embeddings": embedding.await_count,
            "vectors": (list(vectors.deleted), list(vectors.inserted)),
        }
        self.assertEqual(first_snapshot["status"], "ready")

        await worker.process("owner", "kb-1", "doc-1")

        self.assertEqual(
            {
                "status": storage.record.status,
                "chunk_count": storage.record.data.chunk_count,
                "status_writes": list(storage.status_writes),
                "embeddings": embedding.await_count,
                "vectors": (list(vectors.deleted), list(vectors.inserted)),
            },
            first_snapshot,
        )

    async def test_duplicate_after_ready_releases_its_lease(self) -> None:
        """Ignoring a duplicate must not strand the lease it acquired."""
        worker, storage, _, _ = self._build("ready")

        await worker.process("owner", "kb-1", "doc-1")

        self.assertEqual(storage.lease_acquisitions, 1)
        self.assertIsNone(storage.held_by)

    async def test_duplicate_after_ready_survives_a_failing_rerun(
        self,
    ) -> None:
        """A failing backend cannot degrade a ready document via a duplicate.

        This is the damage the issue reports: the second run deletes the
        indexed chunks before re-embedding, so the failure leaves the
        document in ``error`` with nothing indexed.
        """
        worker, storage, embedding, vectors = self._build("pending")
        await worker.process("owner", "kb-1", "doc-1")

        embedding.side_effect = RuntimeError("injected embedding failure")
        await worker.process("owner", "kb-1", "doc-1")

        self.assertEqual(
            {
                "status": storage.record.status,
                "error": storage.record.data.error,
                "chunk_count": storage.record.data.chunk_count,
                "status_writes": list(storage.status_writes),
                "deletes": list(vectors.deleted),
                "inserts": list(vectors.inserted),
            },
            {
                "status": "ready",
                "error": None,
                "chunk_count": 1,
                "status_writes": [
                    "parsing",
                    "chunking",
                    "indexing",
                    "ready",
                ],
                "deletes": ["doc-1"],
                "inserts": ["doc-1"],
            },
        )

    async def test_pending_document_is_still_indexed(self) -> None:
        """The admission check must not swallow a genuine first run."""
        worker, storage, embedding, vectors = self._build("pending")

        await worker.process("owner", "kb-1", "doc-1")

        self.assertEqual(
            {
                "status": storage.record.status,
                "chunk_count": storage.record.data.chunk_count,
                "status_writes": list(storage.status_writes),
                "embeddings": embedding.await_count,
                "deletes": list(vectors.deleted),
                "inserts": list(vectors.inserted),
            },
            {
                "status": "ready",
                "chunk_count": 1,
                "status_writes": [
                    "parsing",
                    "chunking",
                    "indexing",
                    "ready",
                ],
                "embeddings": 1,
                "deletes": ["doc-1"],
                "inserts": ["doc-1"],
            },
        )

    async def test_error_document_is_reindexed(self) -> None:
        """A failed document is reprocessed, only ``ready`` is final.

        Resetting the status is how a caller asks for a re-index, so the
        admission check must stay narrow and not treat ``error`` as done.
        """
        worker, storage, embedding, vectors = self._build("pending")
        embedding.side_effect = RuntimeError("injected embedding failure")

        await worker.process("owner", "kb-1", "doc-1")
        self.assertEqual(storage.record.status, "error")

        embedding.side_effect = None
        await worker.process("owner", "kb-1", "doc-1")

        self.assertEqual(
            {
                "status": storage.record.status,
                "error": storage.record.data.error,
                "chunk_count": storage.record.data.chunk_count,
                "status_writes": list(storage.status_writes),
                "deletes": list(vectors.deleted),
                "inserts": list(vectors.inserted),
            },
            {
                "status": "ready",
                "error": None,
                "chunk_count": 1,
                "status_writes": [
                    "parsing",
                    "chunking",
                    "indexing",
                    "error",
                    "parsing",
                    "chunking",
                    "indexing",
                    "ready",
                ],
                "deletes": ["doc-1", "doc-1"],
                "inserts": ["doc-1"],
            },
        )
