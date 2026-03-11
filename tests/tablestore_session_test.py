# -*- coding: utf-8 -*-
"""Tests for the Tablestore session implementation."""
# pylint: disable=protected-access
from __future__ import annotations

import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.module import StateModule
from agentscope.session._tablestore_session import TablestoreSession


class SimpleStateModule(StateModule):
    """A simple state module for testing."""

    def __init__(self) -> None:
        super().__init__()
        self.name = "test_agent"
        self.value = 42
        self.register_state("name")
        self.register_state("value")


class TablestoreSessionTest(IsolatedAsyncioTestCase):
    """Test cases for the Tablestore session module."""

    def _create_session_with_mocks(self) -> "TablestoreSession":
        """Create a TablestoreSession with mocked dependencies."""
        with patch(
            "agentscope.session._tablestore_session.TablestoreSession"
            "._ensure_initialized",
            new_callable=AsyncMock,
        ):
            # We can't call the real __init__ because it imports tablestore,
            # so we construct the object manually with mocks
            session = object.__new__(TablestoreSession)
            session._tablestore_client = MagicMock()
            session._session_table_name = "test_session"
            session._message_table_name = "test_message"
            session._memory_store = AsyncMock()
            session._memory_store_kwargs = {}
            session._initialized = True
            return session

    async def test_save_session_state(self) -> None:
        """Test saving session state to Tablestore."""
        session = self._create_session_with_mocks()

        session._memory_store.update_session = AsyncMock()

        # Create test state modules
        agent = SimpleStateModule()
        agent.name = "Friday"
        agent.value = 100

        with patch(
            "agentscope.session._tablestore_session.TablestoreSession"
            "._ensure_initialized",
            new_callable=AsyncMock,
        ):
            await session.save_session_state(
                session_id="test_session_1",
                user_id="user_1",
                agent=agent,
            )

        # Verify update_session was called with metadata containing state
        session._memory_store.update_session.assert_called_once()
        saved_session = session._memory_store.update_session.call_args[0][0]
        self.assertEqual(saved_session.session_id, "test_session_1")
        self.assertEqual(saved_session.user_id, "user_1")
        self.assertIn("__state__", saved_session.metadata)

        saved_state = json.loads(saved_session.metadata["__state__"])
        self.assertEqual(saved_state["agent"]["name"], "Friday")
        self.assertEqual(saved_state["agent"]["value"], 100)

    async def test_save_session_state_existing_session(self) -> None:
        """Test saving state to an existing session overwrites old state."""
        session = self._create_session_with_mocks()

        session._memory_store.update_session = AsyncMock()

        # First save
        agent = SimpleStateModule()
        agent.name = "OriginalName"

        with patch(
            "agentscope.session._tablestore_session.TablestoreSession"
            "._ensure_initialized",
            new_callable=AsyncMock,
        ):
            await session.save_session_state(
                session_id="test_session_1",
                user_id="user_1",
                agent=agent,
            )

        # Second save with updated state
        agent.name = "UpdatedName"

        with patch(
            "agentscope.session._tablestore_session.TablestoreSession"
            "._ensure_initialized",
            new_callable=AsyncMock,
        ):
            await session.save_session_state(
                session_id="test_session_1",
                user_id="user_1",
                agent=agent,
            )

        # Verify update_session was called twice (upsert semantics)
        self.assertEqual(
            session._memory_store.update_session.call_count,
            2,
        )

        # Verify the second call contains the updated state
        second_call_session = (
            session._memory_store.update_session.call_args_list[1][0][0]
        )
        saved_state = json.loads(
            second_call_session.metadata["__state__"],
        )
        self.assertEqual(saved_state["agent"]["name"], "UpdatedName")

    async def test_load_session_state(self) -> None:
        """Test loading session state from Tablestore."""
        session = self._create_session_with_mocks()

        # Create state data stored in session metadata
        state_data = {
            "agent": {"name": "Friday", "value": 100},
        }

        mock_session = MagicMock()
        mock_session.metadata = {
            "__state__": json.dumps(state_data),
        }
        session._memory_store.get_session = AsyncMock(
            return_value=mock_session,
        )

        # Create agent and load state
        agent = SimpleStateModule()
        self.assertEqual(agent.name, "test_agent")
        self.assertEqual(agent.value, 42)

        with patch(
            "agentscope.session._tablestore_session.TablestoreSession"
            "._ensure_initialized",
            new_callable=AsyncMock,
        ):
            await session.load_session_state(
                session_id="test_session_1",
                user_id="user_1",
                agent=agent,
            )

        # Verify state was loaded
        self.assertEqual(agent.name, "Friday")
        self.assertEqual(agent.value, 100)

    async def test_load_session_state_not_exist_allowed(self) -> None:
        """Test loading non-existent session with allow_not_exist=True."""
        session = self._create_session_with_mocks()

        session._memory_store.get_session = AsyncMock(return_value=None)

        agent = SimpleStateModule()
        original_name = agent.name

        with patch(
            "agentscope.session._tablestore_session.TablestoreSession"
            "._ensure_initialized",
            new_callable=AsyncMock,
        ):
            # Should not raise
            await session.load_session_state(
                session_id="nonexistent",
                user_id="user_1",
                allow_not_exist=True,
                agent=agent,
            )

        # State should remain unchanged
        self.assertEqual(agent.name, original_name)

    async def test_load_session_state_not_exist_disallowed(self) -> None:
        """Test loading non-existent session with allow_not_exist=False."""
        session = self._create_session_with_mocks()

        session._memory_store.get_session = AsyncMock(return_value=None)

        agent = SimpleStateModule()

        with patch(
            "agentscope.session._tablestore_session.TablestoreSession"
            "._ensure_initialized",
            new_callable=AsyncMock,
        ):
            with self.assertRaises(ValueError):
                await session.load_session_state(
                    session_id="nonexistent",
                    user_id="user_1",
                    allow_not_exist=False,
                    agent=agent,
                )

    async def test_load_session_no_state_data(self) -> None:
        """Test loading session that exists but has no state data."""
        session = self._create_session_with_mocks()

        # Session exists but metadata has no __state__ key
        mock_session = MagicMock()
        mock_session.metadata = {}
        session._memory_store.get_session = AsyncMock(
            return_value=mock_session,
        )

        agent = SimpleStateModule()
        original_name = agent.name

        with patch(
            "agentscope.session._tablestore_session.TablestoreSession"
            "._ensure_initialized",
            new_callable=AsyncMock,
        ):
            await session.load_session_state(
                session_id="test_session_1",
                user_id="user_1",
                allow_not_exist=True,
                agent=agent,
            )

        # State should remain unchanged
        self.assertEqual(agent.name, original_name)

    async def test_close(self) -> None:
        """Test closing the Tablestore session."""
        session = self._create_session_with_mocks()

        mock_store = session._memory_store
        await session.close()

        mock_store.close.assert_called_once()
        self.assertIsNone(session._memory_store)
        self.assertFalse(session._initialized)

    async def test_close_when_not_initialized(self) -> None:
        """Test closing when not initialized does nothing."""
        session = self._create_session_with_mocks()
        session._memory_store = None
        session._initialized = False

        # Should not raise
        await session.close()

    async def test_save_and_load_with_memory_module(self) -> None:
        """Test saving and loading a state module that contains memory."""
        session = self._create_session_with_mocks()

        session._memory_store.update_session = AsyncMock()

        # Create a memory module with messages
        memory = InMemoryMemory()
        await memory.add(Msg("Alice", "Hello!", "user"))

        with patch(
            "agentscope.session._tablestore_session.TablestoreSession"
            "._ensure_initialized",
            new_callable=AsyncMock,
        ):
            await session.save_session_state(
                session_id="test_session_1",
                user_id="user_1",
                memory=memory,
            )

        # Verify the state was serialized correctly in session metadata
        saved_session = session._memory_store.update_session.call_args[0][0]
        saved_state = json.loads(saved_session.metadata["__state__"])
        self.assertIn("memory", saved_state)
        self.assertIn("content", saved_state["memory"])
