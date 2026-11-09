# -*- coding: utf-8 -*-
"""Regression tests for the search result bound."""

from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse
from agentscope.message import TextBlock
from agentscope.rag import KnowledgeBase


class _EmptyStore:
    """Minimal vector store that records the bounds it is asked for."""

    # The stub mirrors the backend signature, so most parameters are
    # intentionally ignored rather than stored.
    # pylint: disable=unused-argument

    def __init__(self) -> None:
        self.bounds: list[int] = []
        self.query_vectors: list[list[float]] = []

    async def create_collection(
        self,
        collection: str,
        dimensions: int,
    ) -> None:
        """Collections are irrelevant for these tests."""

    async def has_collection(self, collection: str) -> bool:
        """Pretend the collection already exists."""
        return True

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list:
        """Record the call instead of hitting a real backend."""
        self.bounds.append(top_k)
        self.query_vectors.append(query_vector)
        return []


class SearchTopKBoundTest(IsolatedAsyncioTestCase):
    """A non-positive top_k must be rejected, never reinterpreted."""

    async def asyncSetUp(self) -> None:
        """Bind a knowledge base to a recording store and a stub model."""
        self.store = _EmptyStore()
        self.embedding = AsyncMock(spec=EmbeddingModelBase)
        self.embedding.dimensions = 3
        self.embedding.supports_multimodal = False
        self.embedding.return_value = EmbeddingResponse(
            embeddings=[[1.0, 0.0, 0.0]],
        )

    def _knowledge(self) -> KnowledgeBase:
        """Bind a knowledge base to the recording store."""
        return KnowledgeBase(
            name="documents",
            description="Test documents",
            embedding_model=self.embedding,
            vector_store=self.store,
            collection="documents",
        )

    async def test_negative_top_k_is_rejected(self) -> None:
        """`-1` must not be read as "all but the last hit" by the slice."""
        with self.assertRaises(ValueError) as raised:
            await self._knowledge().search(
                [TextBlock(text="question")],
                top_k=-1,
            )

        self.assertIn("top_k", str(raised.exception))

    async def test_zero_top_k_is_rejected(self) -> None:
        """A zero bound would always return nothing, which hides a mistake."""
        with self.assertRaises(ValueError) as raised:
            await self._knowledge().search(
                [TextBlock(text="question")],
                top_k=0,
            )

        self.assertIn("top_k", str(raised.exception))

    async def test_rejected_top_k_never_reaches_the_store(self) -> None:
        """The bound is validated before any embedding or store call."""
        with self.assertRaises(ValueError):
            await self._knowledge().search(
                [TextBlock(text="question")],
                top_k=-1,
            )

        self.assertEqual(self.store.query_vectors, [])
        self.embedding.assert_not_called()

    async def test_positive_top_k_still_bounds_the_results(self) -> None:
        """A valid bound keeps working and is passed through unchanged."""
        await self._knowledge().search([TextBlock(text="question")], top_k=3)

        self.assertEqual(self.store.bounds, [3])
