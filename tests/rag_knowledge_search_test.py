# -*- coding: utf-8 -*-
"""Unit tests for concurrent KnowledgeBase searches."""
import asyncio
from typing import Any, Coroutine
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse
from agentscope.message import TextBlock
from agentscope.rag import (
    Chunk,
    KnowledgeBase,
    VectorSearchResult,
    VectorStoreBase,
)


class KnowledgeSearchLifecycleTest(IsolatedAsyncioTestCase):
    """Check the lifetime and results of a two-query search."""

    def setUp(self) -> None:
        model = AsyncMock(spec=EmbeddingModelBase)
        model.supports_multimodal = False
        model.return_value = EmbeddingResponse(embeddings=[[0.0], [1.0]])
        self.store = AsyncMock(spec=VectorStoreBase)
        self.store.has_collection.return_value = True
        self.knowledge = KnowledgeBase("kb", "test", model, self.store, "docs")

    async def _start_blocked_search(
        self,
    ) -> tuple[asyncio.Task, list[asyncio.Future], set[int]]:
        """Return once both queries are waiting for their replies."""
        replies = [asyncio.get_running_loop().create_future() for _ in (0, 1)]
        started = [asyncio.Event(), asyncio.Event()]
        finished: set[int] = set()

        async def search(
            *,
            query_vector: list[float],
            **_kwargs: Any,
        ) -> list[VectorSearchResult]:
            idx = int(query_vector[0])
            started[idx].set()
            try:
                return await replies[idx]
            finally:
                finished.add(idx)

        self.store.search.side_effect = search
        parent = asyncio.create_task(self.knowledge.search(["q0", "q1"]))
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started)),
            timeout=5,
        )
        return parent, replies, finished

    async def test_query_failure_joins_sibling(self) -> None:
        """Either query can fail first and its exception must propagate."""
        for idx in (0, 1):
            with self.subTest(idx=idx):
                parent, replies, finished = await self._start_blocked_search()
                error = RuntimeError("query failed")
                replies[idx].set_exception(error)
                with self.assertRaises(RuntimeError) as ctx:
                    await parent
                self.assertIs(ctx.exception, error)
                self.assertEqual(finished, {0, 1})

    async def test_child_cancellation_joins_sibling(self) -> None:
        """Cancelling one reply must stop both queries."""
        parent, replies, finished = await self._start_blocked_search()
        replies[0].cancel()
        with self.assertRaises(asyncio.CancelledError):
            await parent
        self.assertEqual(finished, {0, 1})

    async def test_parent_cancellation_joins_queries(self) -> None:
        """Both queries must finish before caller cancellation propagates."""
        parent, _, finished = await self._start_blocked_search()
        parent.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await parent
        self.assertEqual(finished, {0, 1})

    async def test_sync_store_error_joins_started_query(self) -> None:
        """A store may validate arguments before returning its coroutine."""
        release = asyncio.Event()
        finished = asyncio.Event()
        error = ValueError("invalid vector")

        async def run_query() -> list[VectorSearchResult]:
            try:
                await release.wait()
                return []
            finally:
                finished.set()

        def search(
            *,
            query_vector: list[float],
            **_kwargs: Any,
        ) -> Coroutine[Any, Any, list[VectorSearchResult]]:
            if query_vector == [1.0]:
                raise error
            return run_query()

        self.store.search = search
        try:
            with self.assertRaises(ValueError) as ctx:
                await self.knowledge.search(["q0", "q1"])
            self.assertIs(ctx.exception, error)
            self.assertTrue(finished.is_set())
        finally:
            release.set()

    async def test_parent_cancellation_during_sibling_cleanup(self) -> None:
        """Repeated caller cancellation must wait for async cleanup."""
        started = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def search(
            *,
            query_vector: list[float],
            **_kwargs: Any,
        ) -> list[VectorSearchResult]:
            if query_vector == [1.0]:
                await started.wait()
                raise RuntimeError("query failed")
            started.set()
            try:
                await asyncio.Event().wait()
                return []
            finally:
                cleaning.set()
                await release.wait()
                finished.set()

        self.store.search.side_effect = search
        parent = asyncio.create_task(self.knowledge.search(["q0", "q1"]))
        try:
            await asyncio.wait_for(cleaning.wait(), timeout=5)
            for _ in range(2):
                parent.cancel()
                await asyncio.sleep(0)
                self.assertFalse(parent.done())
        finally:
            release.set()
            await asyncio.gather(parent, return_exceptions=True)

        self.assertEqual((finished.is_set(), parent.cancelled()), (True, True))

    async def test_search_merges_and_ranks_results(self) -> None:
        """Keep the best duplicate and honor the score and count limits."""
        chunk = Chunk(
            content=TextBlock(text="text"),
            source="test.txt",
            chunk_index=0,
            total_chunks=1,
        )

        def hit(doc: str, score: float) -> VectorSearchResult:
            return VectorSearchResult(
                document_id=doc,
                chunk=chunk,
                score=score,
            )

        best, middle, low = hit("a", 0.9), hit("c", 0.7), hit("b", 0.4)
        batches = [[low, hit("a", 0.8)], [middle, best]]
        self.store.search.side_effect = batches * 2
        self.assertEqual(
            await self.knowledge.search(["q0", "q1"], score_threshold=0.5),
            [best, middle],
        )
        self.assertEqual(
            await self.knowledge.search(["q0", "q1"], top_k=1),
            [best],
        )
