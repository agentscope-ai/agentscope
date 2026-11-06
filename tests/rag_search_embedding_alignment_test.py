# -*- coding: utf-8 -*-
"""Regression tests for query/embedding alignment during search."""

from contextlib import AsyncExitStack
from importlib.util import find_spec
from typing import Any
from unittest import skipUnless
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse
from agentscope.message import TextBlock
from agentscope.rag import KnowledgeBase, QdrantStore


@skipUnless(find_spec("qdrant_client"), "qdrant-client is not installed")
class SearchEmbeddingAlignmentTest(IsolatedAsyncioTestCase):
    """A short embedding response must not silently drop a query."""

    async def asyncSetUp(self) -> None:
        """Bind a knowledge base to an in-memory store and a stub model."""
        exit_stack = AsyncExitStack()
        self.addAsyncCleanup(exit_stack.aclose)
        self.store = await exit_stack.enter_async_context(
            QdrantStore(location=":memory:"),
        )
        await self.store.create_collection("documents", dimensions=3)
        self.embedding = AsyncMock(spec=EmbeddingModelBase)
        self.embedding.dimensions = 3
        self.embedding.supports_multimodal = False

    def _knowledge(self) -> KnowledgeBase:
        """Bind a knowledge base to the test collection."""
        return KnowledgeBase(
            name="documents",
            description="Test documents",
            embedding_model=self.embedding,
            vector_store=self.store,
            collection="documents",
        )

    async def test_short_embedding_response_is_rejected(self) -> None:
        """Two queries with one vector back must not search only one."""
        self.embedding.return_value = EmbeddingResponse(
            embeddings=[[1.0, 0.0, 0.0]],
        )

        with self.assertRaises(RuntimeError) as raised:
            await self._knowledge().search(
                [
                    TextBlock(text="first question"),
                    TextBlock(text="second question"),
                ],
            )

        message = str(raised.exception)
        self.assertIn("1", message)
        self.assertIn("2", message)

    async def test_extra_embedding_response_is_rejected(self) -> None:
        """One query with two vectors back is a contract violation too."""
        self.embedding.return_value = EmbeddingResponse(
            embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        )

        with self.assertRaises(RuntimeError):
            await self._knowledge().search(
                [TextBlock(text="only question")],
            )

    async def test_aligned_embedding_response_searches_every_query(
        self,
    ) -> None:
        """A matching count must still fan out one search per query."""
        self.embedding.return_value = EmbeddingResponse(
            embeddings=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        )
        knowledge = self._knowledge()
        calls: list[list[float]] = []
        original = self.store.search

        async def recording_search(**kwargs: Any) -> list:
            """Record the query vector before delegating."""
            calls.append(kwargs["query_vector"])
            return await original(**kwargs)

        self.store.search = recording_search

        await knowledge.search(
            [
                TextBlock(text="first question"),
                TextBlock(text="second question"),
            ],
        )

        self.assertEqual(len(calls), 2)
