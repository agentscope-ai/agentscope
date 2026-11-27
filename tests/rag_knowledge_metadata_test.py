# -*- coding: utf-8 -*-
"""Regression tests for metadata isolation during document insertion."""
import asyncio
import hashlib
import uuid
from contextlib import AsyncExitStack
from importlib.util import find_spec
from unittest import TestCase, skipUnless
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse
from agentscope.message import TextBlock
from agentscope.rag import (
    Chunk,
    ElasticsearchStore,
    KnowledgeBase,
    MilvusLiteStore,
    MongoDBStore,
    QdrantStore,
    VectorRecord,
)


@skipUnless(find_spec("qdrant_client"), "qdrant-client is not installed")
class KnowledgeMetadataTest(IsolatedAsyncioTestCase):
    """Exercise public insertion and retrieval against in-memory Qdrant."""

    async def asyncSetUp(self) -> None:
        """Create a local store and an embedding model with no API calls."""
        exit_stack = AsyncExitStack()
        self.addAsyncCleanup(exit_stack.aclose)
        self.store = await exit_stack.enter_async_context(
            QdrantStore(location=":memory:"),
        )
        await self.store.create_collection("documents", dimensions=3)
        self.embedding = AsyncMock(spec=EmbeddingModelBase)
        self.embedding.dimensions = 3
        self.embedding.supports_multimodal = False
        self.embedding.return_value = EmbeddingResponse(
            embeddings=[[1.0, 0.0, 0.0]],
        )
        self.chunks = [
            Chunk(
                content=TextBlock(text="Reference document"),
                source="reference.txt",
                chunk_index=0,
                total_chunks=1,
                metadata={"page": 1, "origin": "parser"},
            ),
        ]

    def _knowledge(self, scope: str | None = None) -> KnowledgeBase:
        """Bind a knowledge base to the test collection and optional scope."""
        return KnowledgeBase(
            name=scope or "documents",
            description="Test documents",
            embedding_model=self.embedding,
            vector_store=self.store,
            collection="documents",
            metadata_filter={"corpus": scope} if scope else None,
        )

    async def test_reused_chunks_keep_document_metadata_independent(
        self,
    ) -> None:
        """One insert must not turn its metadata into the next one's input."""
        knowledge = self._knowledge()
        before = [chunk.model_dump() for chunk in self.chunks]
        for revision in ("A", "B"):
            await knowledge.insert_document(
                self.chunks,
                document_id=f"doc-{revision}",
                document_metadata={"revision": revision, "origin": "upload"},
            )

        for revision in ("A", "B"):
            stored = await knowledge.list_chunks(f"doc-{revision}")
            self.assertEqual(len(stored), 1)
            self.assertEqual(
                stored[0].model_dump(),
                {
                    **before[0],
                    "metadata": {
                        "revision": revision,
                        "page": 1,
                        "origin": "parser",
                    },
                },
            )
        self.assertEqual(
            [chunk.model_dump() for chunk in self.chunks],
            before,
        )

    async def test_concurrent_inserts_keep_their_own_scope(self) -> None:
        """Shared input chunks cannot change scope while embedding awaits."""
        barrier = asyncio.Barrier(2)

        async def embed(inputs: list) -> EmbeddingResponse:
            """Pause both inserts at the embedding boundary."""
            await barrier.wait()
            return EmbeddingResponse(
                embeddings=[[1.0, 0.0, 0.0] for _ in inputs],
            )

        self.embedding.side_effect = embed
        self.chunks[0].metadata["corpus"] = "parser-scope"
        before = [chunk.model_dump() for chunk in self.chunks]
        scopes = {scope: self._knowledge(scope) for scope in ("A", "B")}
        await asyncio.wait_for(
            asyncio.gather(
                *(
                    knowledge.insert_document(self.chunks, f"doc-{scope}")
                    for scope, knowledge in scopes.items()
                ),
            ),
            timeout=5,
        )

        for scope, knowledge in scopes.items():
            documents = await knowledge.list_documents()
            self.assertEqual(
                [document.model_dump() for document in documents],
                [
                    {
                        "document_id": f"doc-{scope}",
                        "source": self.chunks[0].source,
                        "chunk_count": len(self.chunks),
                        "metadata": {
                            "page": 1,
                            "origin": "parser",
                            "corpus": scope,
                        },
                    },
                ],
            )
            stored = await knowledge.list_chunks(f"doc-{scope}")
            self.assertEqual(
                [chunk.model_dump() for chunk in stored],
                [
                    {
                        **before[0],
                        "metadata": {
                            "page": 1,
                            "origin": "parser",
                            "corpus": scope,
                        },
                    },
                ],
            )
        self.assertEqual(
            [chunk.model_dump() for chunk in self.chunks],
            before,
        )

    async def test_failed_embedding_does_not_mutate_caller_chunks(
        self,
    ) -> None:
        """A failed insert leaves its reusable input untouched."""
        knowledge = self._knowledge("A")
        before = [chunk.model_dump() for chunk in self.chunks]
        self.embedding.side_effect = RuntimeError("embedding unavailable")

        with self.assertRaisesRegex(RuntimeError, "embedding unavailable"):
            await knowledge.insert_document(
                self.chunks,
                document_id="failed-document",
                document_metadata={"revision": "failed"},
            )

        self.assertEqual(await knowledge.list_documents(), [])
        self.assertEqual(
            [chunk.model_dump() for chunk in self.chunks],
            before,
        )

    async def test_scopes_reusing_a_document_id_keep_their_chunks(
        self,
    ) -> None:
        """Another scope's insert cannot replace chunks with the same ID."""

        async def embed(inputs: list) -> EmbeddingResponse:
            return EmbeddingResponse(
                embeddings=[[1.0, 0.0, 0.0] for _ in inputs],
            )

        self.embedding.side_effect = embed
        alpha, beta = self._knowledge("A"), self._knowledge("B")
        beta_chunks = [
            self.chunks[0].model_copy(update={"chunk_index": i})
            for i in range(3)
        ]
        await beta.insert_document(beta_chunks, "report")
        await alpha.insert_document(self.chunks, "report")
        # Re-inserting within one scope still replaces chunks in place.
        await beta.insert_document(beta_chunks, "report")

        for knowledge, count in ((alpha, 1), (beta, 3)):
            documents = await knowledge.list_documents()
            self.assertEqual(
                [(d.document_id, d.chunk_count) for d in documents],
                [("report", count)],
            )


class RecordScopeIdentityTest(TestCase):
    """Scoped records get distinct IDs; unscoped IDs are unchanged."""

    @staticmethod
    def _record(scope: dict | None) -> VectorRecord:
        return VectorRecord(
            vector=[1.0, 0.0, 0.0],
            document_id="doc",
            chunk=Chunk(
                content=TextBlock(text="text"),
                source="doc.txt",
                chunk_index=0,
                total_chunks=1,
            ),
            scope=scope,
        )

    def test_record_ids(self) -> None:
        """Check every built-in store's record identity."""
        # pylint: disable=protected-access
        legacy_hash = hashlib.sha256(b"doc\x000").hexdigest()
        stores = {
            QdrantStore: (
                "_point_id",
                str(uuid.uuid5(uuid.NAMESPACE_URL, "doc\x000")),
            ),
            MilvusLiteStore: ("_record_id", legacy_hash),
            ElasticsearchStore: ("_record_id", legacy_hash),
            MongoDBStore: ("_record_id", "doc_0"),
        }
        for store, (method, legacy) in stores.items():
            with self.subTest(store=store.__name__):
                record_id = getattr(store, method)
                for unscoped in (None, {}):
                    self.assertEqual(record_id(self._record(unscoped)), legacy)
                alpha = record_id(self._record({"tenant": "a", "project": 2}))
                self.assertNotEqual(alpha, legacy)
                self.assertEqual(
                    alpha,
                    record_id(self._record({"project": 2, "tenant": "a"})),
                )
                self.assertNotEqual(
                    alpha,
                    record_id(self._record({"tenant": "b", "project": 2})),
                )
