# -*- coding: utf-8 -*-
"""The short-term memory tests."""
from unittest.async_case import IsolatedAsyncioTestCase

from sqlalchemy.ext.asyncio import create_async_engine

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

    memory_session: MemoryBase
    """The test memory instance for different session."""

    memory_user: MemoryBase
    """The test memory instance for different user."""

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

        await self.memory.clear()
        msgs = await self.memory.get_memory()
        self.assertEqual(
            len(msgs),
            0,
        )

    async def _multi_tenant_tests(self) -> None:
        """Test the multi-tenant functionalities of the short-term memory."""
        await self.memory.add(self.msgs[:8])
        msgs = await self.memory.get_memory()
        self.assertEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(8)],
        )

        # Add some msgs with overlapping ids to different users' memory
        await self.memory_user.add(self.msgs[3:])
        self.assertEqual(
            [_.id for _ in await self.memory_user.get_memory()],
            [str(_) for _ in [3, 4, 5, 6, 7, 8, 9]],
        )

        # Mark messages
        await self.memory.update_messages_mark(
            new_mark="shared",
            msg_ids=["5", "6", "7"],
        )

        # mark messages with same ids with different mark for different users
        await self.memory_user.update_messages_mark(
            new_mark="shared_user",
            msg_ids=["6", "7", "8", "9"],
        )

        # Test if the marks are isolated between different users
        msgs = await self.memory.get_memory(
            mark="shared",
        )
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in [5, 6, 7]],
        )
        msgs = await self.memory.get_memory(
            mark="shared_user",
        )
        self.assertEqual(
            len(msgs),
            0,
        )

        msgs = await self.memory_user.get_memory(
            mark="shared_user",
        )
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in [6, 7, 8, 9]],
        )
        msgs = await self.memory_user.get_memory(
            mark="shared",
        )
        self.assertEqual(
            len(msgs),
            0,
        )

        # Test delete operation is isolated between different sessions
        await self.memory.delete(
            msg_ids=["6", "7"],
        )
        msgs = await self.memory.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(6)],
        )
        msgs = await self.memory_user.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(3, 10)],
        )

        # Test delete operation by mark is isolated between different sessions
        await self.memory_user.delete_by_mark("shared")
        msgs = await self.memory_user.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(3, 10)],
        )
        msgs = await self.memory.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(6)],
        )

        # Clean up
        await self.memory.clear()
        await self.memory_user.clear()

    async def _multi_session_tests(self) -> None:
        """Test the multi-session functionalities of the short-term memory."""
        await self.memory.add(self.msgs[:8])
        msgs = await self.memory.get_memory()
        self.assertEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(8)],
        )

        # Add some msgs with overlapping ids to different session's memory
        await self.memory_session.add(self.msgs[3:])
        self.assertEqual(
            [_.id for _ in await self.memory_session.get_memory()],
            [str(_) for _ in [3, 4, 5, 6, 7, 8, 9]],
        )

        # Mark messages in first session
        await self.memory.update_messages_mark(
            new_mark="session1_mark",
            msg_ids=["5", "6", "7"],
        )

        # mark messages with same ids with different mark for different session
        await self.memory_session.update_messages_mark(
            new_mark="session2_mark",
            msg_ids=["6", "7", "8", "9"],
        )

        # Test if the marks are isolated between different sessions
        msgs = await self.memory.get_memory(
            mark="session1_mark",
        )
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in [5, 6, 7]],
        )
        msgs = await self.memory.get_memory(
            mark="session2_mark",
        )
        self.assertEqual(
            len(msgs),
            0,
        )

        msgs = await self.memory_session.get_memory(
            mark="session2_mark",
        )
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in [6, 7, 8, 9]],
        )
        msgs = await self.memory_session.get_memory(
            mark="session1_mark",
        )
        self.assertEqual(
            len(msgs),
            0,
        )

        # Test delete operation is isolated between different sessions
        await self.memory.delete(
            msg_ids=["6", "7"],
        )
        msgs = await self.memory.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(6)],
        )
        msgs = await self.memory_session.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(3, 10)],
        )

        # Test delete operation by mark is isolated between different sessions
        await self.memory_session.delete_by_mark("session1_mark")
        msgs = await self.memory_session.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(3, 10)],
        )
        msgs = await self.memory.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(6)],
        )

        # Clean up
        await self.memory.clear()
        await self.memory_session.clear()

    async def _test_add_duplicated_msgs(self) -> None:
        """Test adding duplicated messages to the memory."""
        await self.memory.add(self.msgs[:8])
        await self.memory.add(self.msgs[5:])

        msgs = await self.memory.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(10)],
        )

        await self.memory.clear()

    async def _test_delete_nonexistent_msg(self) -> None:
        """Test deleting nonexistent messages from the memory."""
        await self.memory.add(self.msgs[:5])
        await self.memory.delete(msg_ids=["nonexistent_id"])
        msgs = await self.memory.get_memory()
        self.assertListEqual(
            [_.id for _ in msgs],
            [str(_) for _ in range(5)],
        )

        await self.memory.clear()

    async def asyncTearDown(self) -> None:
        """Clean up after unittests"""
        await self.memory.clear()
        # Close the session or connection if applicable
        if hasattr(self.memory, "close"):
            await self.memory.close()


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
        await self._test_add_duplicated_msgs()
        await self._test_delete_nonexistent_msg()


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
            session_id="session_1",
            user_id="user_1",
            engine_or_session=self.engine,
        )

        self.memory_session = AsyncSQLAlchemyMemory(
            session_id="session_2",
            user_id="user_1",
            engine_or_session=self.engine,
        )

        self.memory_user = AsyncSQLAlchemyMemory(
            session_id="session_2",
            user_id="user_2",
            engine_or_session=self.engine,
        )

    async def test_memory(self) -> None:
        """Test the SQLAlchemy memory functionalities."""
        await self._basic_tests()
        await self._test_add_duplicated_msgs()
        await self._test_delete_nonexistent_msg()
        await self._mark_tests()
        await self._multi_tenant_tests()
        await self._multi_session_tests()

    async def asyncTearDown(self) -> None:
        """Clean up after unittests"""
        await super().asyncTearDown()
        await self.engine.dispose()


class RedisMemoryTest(ShortTermMemoryTest):
    """The Redis short-term memory tests."""

    memory: RedisMemory
    """The Redis memory instance."""

    memory_session: RedisMemory
    """The Redis memory instance for different session."""

    memory_user: RedisMemory
    """The Redis memory instance for different user."""

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
            user_id="user_1",
            session_id="session_1",
            connection_pool=fake_redis.connection_pool,
        )

        self.memory_session = RedisMemory(
            user_id="user_1",
            session_id="session_2",
            connection_pool=fake_redis.connection_pool,
        )

        self.memory_user = RedisMemory(
            user_id="user_2",
            session_id="session_2",
            connection_pool=fake_redis.connection_pool,
        )

    async def test_memory(self) -> None:
        """Test the Redis memory functionalities."""
        await self._basic_tests()
        await self._mark_tests()
        await self._test_add_duplicated_msgs()
        await self._test_delete_nonexistent_msg()
        await self._multi_tenant_tests()
        await self._multi_session_tests()
