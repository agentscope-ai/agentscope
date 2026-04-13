# -*- coding: utf-8 -*-
"""Unit tests for LocalJSONStorage."""
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.storage import LocalJSONStorage
from agentscope.message import Msg
from agentscope.agent import AgentState


class LocalJSONStorageTest(IsolatedAsyncioTestCase):
    """Test cases for LocalJSONStorage."""

    async def asyncSetUp(self) -> None:
        """Setup temporary storage for each test."""
        self.tmpdir = tempfile.TemporaryDirectory()
        self.storage = LocalJSONStorage(root_dir=self.tmpdir.name)

    async def asyncTearDown(self) -> None:
        """Cleanup temporary storage."""
        self.tmpdir.cleanup()

    async def test_session_operations(self) -> None:
        """Test session creation, listing, and deletion."""
        # Create sessions
        session_id1 = await self.storage.upsert_session(user_id="user1")
        session_id2 = await self.storage.upsert_session(user_id="user1")
        session_id3 = await self.storage.upsert_session(user_id="user2")

        self.assertNotEqual(session_id1, session_id2)

        # List sessions
        sessions_user1 = await self.storage.list_sessions(user_id="user1")
        sessions_user2 = await self.storage.list_sessions(user_id="user2")

        self.assertEqual(len(sessions_user1), 2)
        self.assertEqual(len(sessions_user2), 1)
        self.assertIn(session_id1, sessions_user1)
        self.assertIn(session_id2, sessions_user1)
        self.assertIn(session_id3, sessions_user2)

        # Delete session
        await self.storage.delete_session(session_id1, user_id="user1")
        sessions_user1 = await self.storage.list_sessions(user_id="user1")
        self.assertEqual(len(sessions_user1), 1)
        self.assertNotIn(session_id1, sessions_user1)

    async def test_history_operations(self) -> None:
        """Test message history upsert and retrieval."""
        session_id = await self.storage.upsert_session(user_id="default")

        # Create messages
        msgs = [
            Msg(name="user", content="Hello", role="user"),
            Msg(name="assistant", content="Hi there", role="assistant"),
            Msg(name="user", content="How are you?", role="user"),
        ]

        # Upsert history
        await self.storage.upsert_history(session_id, msgs, user_id="default")

        # Get history
        history = await self.storage.get_history(
            session_id,
            limit=10,
            user_id="default",
        )
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0].content, "Hello")
        self.assertEqual(history[1].content, "Hi there")
        self.assertEqual(history[2].content, "How are you?")

        # Test limit
        history_limited = await self.storage.get_history(
            session_id,
            limit=2,
            user_id="default",
        )
        self.assertEqual(len(history_limited), 2)

    async def test_state_operations(self) -> None:
        """Test agent state update and retrieval."""
        session_id = await self.storage.upsert_session(user_id="default")
        agent_id = "agent_001"

        # Create state
        state = AgentState(
            summary="Test summary",
            context=[Msg(name="user", content="Test", role="user")],
            cur_iter=1,
        )

        # Update state
        await self.storage.update_state(
            session_id,
            agent_id,
            state,
            user_id="default",
        )

        # Get state
        retrieved_state = await self.storage.get_state(
            session_id,
            agent_id,
            user_id="default",
        )
        self.assertEqual(retrieved_state.summary, "Test summary")
        self.assertEqual(retrieved_state.cur_iter, 1)
        self.assertEqual(len(retrieved_state.context), 1)
        self.assertEqual(retrieved_state.context[0].content, "Test")

    async def test_user_isolation(self) -> None:
        """Test that different users have isolated data."""
        # User1 creates session and adds history
        session_id1 = await self.storage.upsert_session(user_id="user1")
        msgs1 = [Msg(name="user", content="User1 message", role="user")]
        await self.storage.upsert_history(session_id1, msgs1, user_id="user1")

        # User2 creates session and adds history
        session_id2 = await self.storage.upsert_session(user_id="user2")
        msgs2 = [Msg(name="user", content="User2 message", role="user")]
        await self.storage.upsert_history(session_id2, msgs2, user_id="user2")

        # Verify isolation
        sessions_user1 = await self.storage.list_sessions(user_id="user1")
        sessions_user2 = await self.storage.list_sessions(user_id="user2")

        self.assertIn(session_id1, sessions_user1)
        self.assertNotIn(session_id1, sessions_user2)
        self.assertIn(session_id2, sessions_user2)
        self.assertNotIn(session_id2, sessions_user1)

        history1 = await self.storage.get_history(
            session_id1,
            limit=10,
            user_id="user1",
        )
        history2 = await self.storage.get_history(
            session_id2,
            limit=10,
            user_id="user2",
        )

        self.assertEqual(history1[0].content, "User1 message")
        self.assertEqual(history2[0].content, "User2 message")
