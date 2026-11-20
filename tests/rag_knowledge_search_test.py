# -*- coding: utf-8 -*-
"""Unit tests for KnowledgeBase concurrent search task lifecycles."""
import asyncio
from typing import Any, Callable
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse
from agentscope.message import Base64Source, DataBlock, TextBlock
from agentscope.rag import (
    Chunk,
    KnowledgeBase,
    VectorSearchResult,
    VectorStoreBase,
)


class KnowledgeSearchLifecycleTest(IsolatedAsyncioTestCase):
    """Search queries must cancel and await sibling tasks on failure."""

    def _setup_knowledge(
        self,
        embeddings: list[list[float]],
        search_impl: Callable[..., Any],
        supports_multimodal: bool = False,
    ) -> tuple[KnowledgeBase, AsyncMock, AsyncMock]:
        """Create a knowledge base with mock model and store."""
        model = AsyncMock(spec=EmbeddingModelBase)
        model.supports_multimodal = supports_multimodal
        model.dimensions = len(embeddings[0]) if embeddings else 3
        model.return_value = EmbeddingResponse(embeddings=embeddings)

        store = AsyncMock(spec=VectorStoreBase)
        store.has_collection = AsyncMock(return_value=True)
        store.search = search_impl

        knowledge = KnowledgeBase(
            name="test_kb",
            description="Test knowledge base",
            embedding_model=model,
            vector_store=store,
            collection="test_collection",
        )
        return knowledge, model, store

    async def _check_query_failure(self, fail_idx: int) -> None:
        """Assert sibling queries are cancelled and joined on failure."""
        started = [asyncio.Event(), asyncio.Event()]
        finished = [asyncio.Event(), asyncio.Event()]
        query_tasks: list[asyncio.Task] = []
        expected_error = RuntimeError(f"query {fail_idx} failed")

        async def mock_search(
            *,
            query_vector: list[float],
            **_kwargs: Any,
        ) -> list[VectorSearchResult]:
            task = asyncio.current_task()
            assert task is not None
            query_tasks.append(task)
            idx = int(query_vector[0])
            started[idx].set()
            try:
                if idx == fail_idx:
                    other_idx = 1 - fail_idx
                    await started[other_idx].wait()
                    raise expected_error
                await asyncio.Event().wait()
                return []
            finally:
                finished[idx].set()

        knowledge, _, _ = self._setup_knowledge(
            embeddings=[[0.0, 0.0], [1.0, 0.0]],
            search_impl=mock_search,
        )

        with self.assertRaises(RuntimeError) as ctx:
            await knowledge.search(["q0", "q1"])

        self.assertIs(ctx.exception, expected_error)
        other_idx = 1 - fail_idx
        self.assertTrue(
            finished[other_idx].is_set(),
            f"Sibling query {other_idx} was not awaited before return",
        )
        self.assertTrue(
            all(t.done() for t in query_tasks),
            "Not all spawned query tasks were completed",
        )

    async def test_query_failure_cancels_and_waits_for_sibling_queries(
        self,
    ) -> None:
        """When one vector query fails, siblings are cancelled and joined."""
        for fail_idx in (0, 1):
            with self.subTest(fail_idx=fail_idx):
                await self._check_query_failure(fail_idx)

    async def test_parent_cancellation_cancels_and_waits_for_query_tasks(
        self,
    ) -> None:
        """Parent cancellation cancels and joins child vector queries."""
        started = [asyncio.Event(), asyncio.Event()]
        finished = [asyncio.Event(), asyncio.Event()]
        query_tasks: list[asyncio.Task] = []

        async def mock_search(
            *,
            query_vector: list[float],
            **_kwargs: Any,
        ) -> list[VectorSearchResult]:
            task = asyncio.current_task()
            assert task is not None
            query_tasks.append(task)
            idx = int(query_vector[0])
            started[idx].set()
            try:
                await asyncio.Event().wait()
                return []
            finally:
                finished[idx].set()

        knowledge, _, _ = self._setup_knowledge(
            embeddings=[[0.0, 0.0], [1.0, 0.0]],
            search_impl=mock_search,
        )

        search_task = asyncio.create_task(knowledge.search(["q0", "q1"]))
        await asyncio.gather(*(event.wait() for event in started))
        search_task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await search_task

        self.assertTrue(finished[0].is_set())
        self.assertTrue(finished[1].is_set())
        self.assertTrue(all(t.done() for t in query_tasks))

    async def test_child_cancellation_cancels_and_waits_for_sibling(
        self,
    ) -> None:
        """Child cancellation cancels and joins sibling vector queries."""
        started = [asyncio.Event(), asyncio.Event()]
        finished = [asyncio.Event(), asyncio.Event()]
        query_tasks: list[asyncio.Task] = []

        async def mock_search(
            *,
            query_vector: list[float],
            **_kwargs: Any,
        ) -> list[VectorSearchResult]:
            task = asyncio.current_task()
            assert task is not None
            query_tasks.append(task)
            idx = int(query_vector[0])
            started[idx].set()
            try:
                if idx == 0:
                    await started[1].wait()
                    raise asyncio.CancelledError()
                await asyncio.Event().wait()
                return []
            finally:
                finished[idx].set()

        knowledge, _, _ = self._setup_knowledge(
            embeddings=[[0.0, 0.0], [1.0, 0.0]],
            search_impl=mock_search,
        )

        with self.assertRaises(asyncio.CancelledError):
            await knowledge.search(["q0", "q1"])

        self.assertTrue(finished[0].is_set())
        self.assertTrue(finished[1].is_set())
        self.assertTrue(all(t.done() for t in query_tasks))

    async def test_successful_search_ranking_and_deduplication(self) -> None:
        """Preserve ranking, deduplication, and thresholding semantics."""
        chunk_a = Chunk(
            content=TextBlock(text="Chunk A"),
            source="a.txt",
            chunk_index=0,
            total_chunks=1,
        )
        chunk_b = Chunk(
            content=TextBlock(text="Chunk B"),
            source="b.txt",
            chunk_index=0,
            total_chunks=1,
        )
        chunk_c = Chunk(
            content=TextBlock(text="Chunk C"),
            source="c.txt",
            chunk_index=0,
            total_chunks=1,
        )

        # Query 0 returns chunk A (0.8) and chunk B (0.4)
        # Query 1 returns chunk A (0.9, higher) and chunk C (0.7)
        res_q0 = [
            VectorSearchResult(document_id="doc_a", chunk=chunk_a, score=0.8),
            VectorSearchResult(document_id="doc_b", chunk=chunk_b, score=0.4),
        ]
        res_q1 = [
            VectorSearchResult(document_id="doc_a", chunk=chunk_a, score=0.9),
            VectorSearchResult(document_id="doc_c", chunk=chunk_c, score=0.7),
        ]

        async def mock_search(
            *,
            query_vector: list[float],
            **_kwargs: Any,
        ) -> list[VectorSearchResult]:
            if query_vector[0] == 0.0:
                return res_q0
            return res_q1

        knowledge, _, _ = self._setup_knowledge(
            embeddings=[[0.0], [1.0]],
            search_impl=mock_search,
        )

        # Case 1: All results, deduplicated by (doc_id, chunk_index), max score
        results = await knowledge.search(["q0", "q1"], top_k=5)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].document_id, "doc_a")
        self.assertEqual(results[0].score, 0.9)
        self.assertEqual(results[1].document_id, "doc_c")
        self.assertEqual(results[1].score, 0.7)
        self.assertEqual(results[2].document_id, "doc_b")
        self.assertEqual(results[2].score, 0.4)

        # Case 2: Score threshold filters low scores
        results_filtered = await knowledge.search(
            ["q0", "q1"],
            top_k=5,
            score_threshold=0.5,
        )
        self.assertEqual(len(results_filtered), 2)
        self.assertEqual(results_filtered[0].document_id, "doc_a")
        self.assertEqual(results_filtered[1].document_id, "doc_c")

        # Case 3: top_k limits returned items
        results_top1 = await knowledge.search(["q0", "q1"], top_k=1)
        self.assertEqual(len(results_top1), 1)
        self.assertEqual(results_top1[0].document_id, "doc_a")

    async def test_empty_and_multimodal_queries(self) -> None:
        """Empty queries or queries with dropped multimodal return empty."""
        knowledge, model, store = self._setup_knowledge(
            embeddings=[],
            search_impl=AsyncMock(),
            supports_multimodal=False,
        )

        # Empty input
        res1 = await knowledge.search([])
        self.assertEqual(res1, [])
        model.assert_not_called()
        store.search.assert_not_called()

        # Non-multimodal model drops DataBlock
        datablock = DataBlock(
            source=Base64Source(data="aGk=", media_type="image/png"),
        )
        res2 = await knowledge.search([datablock])
        self.assertEqual(res2, [])
        model.assert_not_called()
        store.search.assert_not_called()
