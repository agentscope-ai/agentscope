# -*- coding: utf-8 -*-
"""Concurrent initialization and retry tests for knowledge-base handles."""
import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from agentscope.embedding import EmbeddingModelBase
from agentscope.rag import KnowledgeBase, VectorStoreBase


class KnowledgeBaseLifecycleTest(IsolatedAsyncioTestCase):
    """A shared handle should initialize once without preventing retries."""

    def setUp(self) -> None:
        """Use an asynchronous store without any external service."""
        self.store = AsyncMock(spec=VectorStoreBase)
        self.store.has_collection.return_value = False
        model = Mock(spec=EmbeddingModelBase)
        model.dimensions = 3
        self.kb = KnowledgeBase(
            name="test",
            description="Test knowledge base",
            embedding_model=model,
            vector_store=self.store,
            collection="test",
        )

    async def test_concurrent_initialization_creates_once(self) -> None:
        """A second caller waits while the first creation is in flight."""
        entered = asyncio.Event()
        release = asyncio.Event()
        exists = False

        async def create(name: str, dimensions: int) -> None:
            nonlocal exists
            self.assertEqual((name, dimensions), ("test", 3))
            entered.set()
            await release.wait()
            if exists:
                raise RuntimeError("Collection already exists")
            exists = True

        self.store.create_collection.side_effect = create
        first = asyncio.create_task(self.kb.ensure_collection())
        await entered.wait()
        second = asyncio.create_task(self.kb.ensure_collection())
        # Let the second caller enter while the first is blocked on I/O.
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)

        self.assertEqual(results, [None, None])
        self.store.create_collection.assert_awaited_once_with(
            "test",
            dimensions=3,
        )
        await self.kb.ensure_collection()
        self.store.has_collection.assert_awaited_once_with("test")

    async def test_failed_creation_can_retry(self) -> None:
        """A failed creation must not mark the collection ready."""
        self.store.create_collection.side_effect = [
            RuntimeError("offline"),
            None,
        ]
        with self.assertRaisesRegex(RuntimeError, "offline"):
            await self.kb.ensure_collection()
        await self.kb.ensure_collection()
        await self.kb.ensure_collection()
        self.assertEqual(self.store.create_collection.await_count, 2)

    async def test_cancelled_creation_releases_waiter(self) -> None:
        """Cancellation releases initialization so a waiting call can retry."""
        entered = asyncio.Event()

        async def create(name: str, dimensions: int) -> None:
            self.assertEqual((name, dimensions), ("test", 3))
            if self.store.create_collection.await_count == 1:
                entered.set()
                await asyncio.Event().wait()

        self.store.create_collection.side_effect = create
        first = asyncio.create_task(self.kb.ensure_collection())
        await entered.wait()
        second = asyncio.create_task(self.kb.ensure_collection())
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(second, timeout=2)
        await self.kb.ensure_collection()
        self.assertEqual(self.store.create_collection.await_count, 2)

    async def test_existing_collection_is_not_created(self) -> None:
        """An existing collection is cached without another creation."""
        self.store.has_collection.return_value = True
        await self.kb.ensure_collection()
        await self.kb.ensure_collection()
        self.store.create_collection.assert_not_awaited()
        self.store.has_collection.assert_awaited_once_with("test")
