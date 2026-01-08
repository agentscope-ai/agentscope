# -*- coding: utf-8 -*-
"""The short-term memory tests."""
from unittest.async_case import IsolatedAsyncioTestCase

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agentscope.memory import (
    MemoryBase,
    InMemoryMemory,
    AsyncSQLAlchemyMemory,
    RedisMemory,
)
from agentscope.message import Msg


class ShortTermMemoryTest(IsolatedAsyncioTestCase):
    """The short-term memory tests."""

    memory: MemoryBase
    """The test memory instance."""

    async def asyncSetUp(self) -> None:
        """Set up the memory instance for testing."""
        self.msgs = [
            Msg("user", "0", "user"),
            Msg("user", "1", "user"),
            Msg("assistant", "2", "assistant"),
            Msg("system", "3", "system"),
            Msg("user", "4", "user"),
            Msg("assistant", "5", "assistant"),
            Msg("system", "6", "system"),
            Msg("user", "7", "user"),
            Msg("assistant", "8", "assistant"),
            Msg("system", "9", "system"),
        ]
        for i, msg in enumerate(self.msgs):
            msg.id = str(i)

    async def _basic_tests(self) -> None:
        """Test the basic functionalities of the short-term memory."""
        # test at the beginning
        self.assertIsInstance(await self.memory.get_memory(), list)
        self.assertEqual(
            len(await self.memory.get_memory()),
            0,
        )
        self.assertEqual(
            await self.memory.size(),
            0,
        )

        await self.memory.update_compressed_summary("abc")
        self.assertEqual(
            len(await self.memory.get_memory()),
            1,
        )

        await self.memory.update_compressed_summary("")
        self.assertEqual(
            len(await self.memory.get_memory()),
            0,
        )

        # test adding messages
        await self.memory.add(self.msgs[:5])
        msgs = await self.memory.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(5)],
        )

        # test deleting messages by id
        await self.memory.delete(msg_ids=["2", "4"])
        msgs = await self.memory.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            ["0", "1", "3"],
        )
        self.assertEqual(
            await self.memory.size(),
            3,
        )

        # test adding more messages
        await self.memory.add(self.msgs[5:])
        msgs = await self.memory.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in [0, 1, 3, 5, 6, 7, 8, 9]],
        )

        # test clearing memory
        await self.memory.clear()
        self.assertEqual(
            await self.memory.size(),
            0,
        )

    async def _mark_tests(self) -> None:
        """Test the mark-related functionalities of the short-term memory."""
        # test getting messages by nonexistent mark
        await self.memory.add(self.msgs[:5])
        self.assertListEqual(
            [_.id for _ in await self.memory.get_memory()],
            [str(_) for _ in range(5)],
        )
        self.assertEqual(
            len(await self.memory.get_memory(mark="nonexistent")),
            0,
        )

        # test adding marked messages
        await self.memory.add(
            self.msgs[5:7],
            marks=["important", "todo"],
        )
        await self.memory.add(self.msgs[7:], marks="important")

        # Test get messages by "important" mark
        msgs = await self.memory.get_memory(mark="important")
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(5, 10)],
        )

        # Test get messages by "todo" mark
        msgs = await self.memory.get_memory(mark="todo")
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(5, 7)],
        )

        # Test get messages excluding "todo" mark
        msgs = await self.memory.get_memory(exclude_mark="todo")
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in [0, 1, 2, 3, 4, 7, 8, 9]],
        )

        msgs = await self.memory.get_memory(exclude_mark="important")
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in [0, 1, 2, 3, 4]],
        )

        # add unmarked messages
        msgs = [
            Msg("user", "10", "user"),
            Msg("user", "11", "user"),
        ]
        msgs[0].id = "10"
        msgs[1].id = "11"
        await self.memory.add(msgs)
        msgs = await self.memory.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(12)],
        )

        # test marking messages
        await self.memory.update_messages_mark(
            msg_ids=["0", "1", "2"],
            new_mark="review",
        )
        msgs = await self.memory.get_memory(mark="review")
        self.assertListEqual(
            [_.id for _ in msgs],
            ["0", "1", "2"],
        )

        # test adding multiple marks to messages
        await self.memory.update_messages_mark(
            msg_ids=["6", "7", "9"],
            new_mark="unread",
        )
        msgs = await self.memory.get_memory(mark="unread")
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in [6, 7, 9]],
        )
        msgs = await self.memory.get_memory(mark="important")
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in [5, 6, 7, 8, 9]],
        )

        # test unmarking messages
        await self.memory.update_messages_mark(
            msg_ids=["5", "7"],
            old_mark="important",
            new_mark=None,
        )
        self.assertListEqual(
            [_.id for _ in await self.memory.get_memory(mark="important")],
            [str(_) for _ in [6, 8, 9]],
        )

        # test updating marks
        await self.memory.update_messages_mark(
            msg_ids=["6", "8"],
            old_mark="important",
            new_mark="archived",
        )
        self.assertListEqual(
            [_.id for _ in await self.memory.get_memory(mark="important")],
            ["9"],
        )
        self.assertListEqual(
            [_.id for _ in await self.memory.get_memory(mark="archived")],
            [str(_) for _ in [6, 8]],
        )

        # test deleting messages by mark
        await self.memory.delete_by_mark("important")
        msgs = await self.memory.get_memory(mark="important")
        self.assertListEqual(
            [_.id for _ in msgs],
            [],
        )
        msgs = await self.memory.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11]],
        )

        await self.memory.delete_by_mark(["review", "archived"])
        msgs = await self.memory.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in [3, 4, 5, 7, 10, 11]],
        )

    async def _multi_tenant_tests(self) -> None:
        """Test the multi-tenant functionalities of the short-term memory."""

    async def asyncTearDown(self) -> None:
        """Clean up after unittests"""
        await self.memory.clear()


class InMemoryMemoryTest(ShortTermMemoryTest):
    """The in-memory short-term memory tests."""

    async def asyncSetUp(self) -> None:
        """Set up the in-memory memory instance for testing."""
        await super().asyncSetUp()
        self.memory = InMemoryMemory()

    async def test_memory(self) -> None:
        """Test the in-memory memory functionalities."""
        await self._basic_tests()
        await self._mark_tests()


class AsyncSQLAlchemyMemoryTest(ShortTermMemoryTest):
    """The SQLAlchemy short-term memory tests."""

    async def asyncSetUp(self) -> None:
        """Set up the SQLAlchemy memory instance for testing."""
        await super().asyncSetUp()
        self.engine = create_async_engine(
            # in-memory SQLite database for testing
            url="sqlite+aiosqlite:///:memory:",
        )
        self.memory = AsyncSQLAlchemyMemory(
            engine=self.engine,
        )

    async def test_memory(self) -> None:
        """Test the SQLAlchemy memory functionalities."""
        await self._basic_tests()
        await self._mark_tests()

    async def asyncTearDown(self) -> None:
        """Clean up the SQLAlchemy memory instance after testing."""
        await super().asyncTearDown()
        if isinstance(self.engine, AsyncEngine):
            await self.engine.dispose()


class RedisMemoryTest(ShortTermMemoryTest):
    """The Redis short-term memory tests."""

    async def asyncSetUp(self) -> None:
        """Set up the Redis memory instance for testing."""
        await super().asyncSetUp()
        try:
            import fakeredis.aioredis
        except ImportError:
            self.skipTest(
                "fakeredis is not installed. Install it via "
                "'pip install fakeredis' to run this test.",
            )

        # Use fakeredis for in-memory testing without a real Redis server
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.memory = RedisMemory(
            connection_pool=fake_redis.connection_pool,
        )

    async def test_memory(self) -> None:
        """Test the Redis memory functionalities."""
        await self._basic_tests()
        await self._mark_tests()

    async def asyncTearDown(self) -> None:
        """Clean up the Redis memory instance after testing."""
        await super().asyncTearDown()
        # Close the client connection by get_client method from the memory
        client = await self.memory.get_client()
        await client.close()
