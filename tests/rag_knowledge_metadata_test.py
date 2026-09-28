# -*- coding: utf-8 -*-
"""Regression tests for metadata isolation during document insertion."""
import asyncio
from contextlib import AsyncExitStack
from importlib.util import find_spec
from unittest import skipUnless
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse
from agentscope.message import TextBlock
from agentscope.rag import Chunk, KnowledgeBase, QdrantStore


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
                [document.document_id for document in documents],
                [f"doc-{scope}"],
            )
            stored = await knowledge.list_chunks(f"doc-{scope}")
            self.assertEqual(
                [chunk.metadata for chunk in stored],
                [{"page": 1, "origin": "parser", "corpus": scope}],
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
