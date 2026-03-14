# -*- coding: utf-8 -*-
"""Tests for the Tablestore session implementation."""
# pylint: disable=protected-access
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any
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

        # Mock get_session to return None (new session)
        session._memory_store.get_session = AsyncMock(return_value=None)
        session._memory_store.put_session = AsyncMock()
        session._memory_store.put_message = AsyncMock()

        # Mock list_messages to return empty iterator
        async def empty_iterator() -> AsyncGenerator[None, None]:
            return
            yield  # make it an async generator

        session._memory_store.list_messages = AsyncMock(
            return_value=empty_iterator(),
        )

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

        # Verify put_session was called (new session created)
        session._memory_store.put_session.assert_called_once()

        # Verify put_message was called with serialized state
        session._memory_store.put_message.assert_called_once()
        saved_message = session._memory_store.put_message.call_args[0][0]
        self.assertEqual(saved_message.message_id, "__state__")

        saved_state = json.loads(saved_message.content)
        self.assertEqual(saved_state["agent"]["name"], "Friday")
        self.assertEqual(saved_state["agent"]["value"], 100)

    async def test_save_session_state_existing_session(self) -> None:
        """Test saving state to an existing session clears old messages."""
        session = self._create_session_with_mocks()

        # Mock get_session to return existing session
        mock_existing_session = MagicMock()
        session._memory_store.get_session = AsyncMock(
            return_value=mock_existing_session,
        )
        session._memory_store.put_message = AsyncMock()
        session._memory_store.delete_message = AsyncMock()

        # Mock list_messages to return one existing message
        existing_msg = MagicMock()
        existing_msg.message_id = "__state__"

        async def existing_iterator() -> AsyncGenerator[Any, None]:
            yield existing_msg

        session._memory_store.list_messages = AsyncMock(
            return_value=existing_iterator(),
        )

        agent = SimpleStateModule()

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

        # Verify old message was deleted
        session._memory_store.delete_message.assert_called_once_with(
            session_id="test_session_1",
            message_id="__state__",
        )

        # Verify put_session was NOT called (session already exists)
        session._memory_store.put_session.assert_not_called()

    async def test_load_session_state(self) -> None:
        """Test loading session state from Tablestore."""
        session = self._create_session_with_mocks()

        # Mock get_session to return existing session
        mock_session = MagicMock()
        session._memory_store.get_session = AsyncMock(
            return_value=mock_session,
        )

        # Create state data
        state_data = {
            "agent": {"name": "Friday", "value": 100},
        }

        # Mock list_messages to return state message
        state_msg = MagicMock()
        state_msg.message_id = "__state__"
        state_msg.content = json.dumps(state_data)

        async def message_iterator() -> AsyncGenerator[Any, None]:
            yield state_msg

        session._memory_store.list_messages = AsyncMock(
            return_value=message_iterator(),
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

        mock_session = MagicMock()
        session._memory_store.get_session = AsyncMock(
            return_value=mock_session,
        )

        # Empty message list
        async def empty_iterator() -> AsyncGenerator[None, None]:
            return
            yield

        session._memory_store.list_messages = AsyncMock(
            return_value=empty_iterator(),
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

        # Mock for save
        session._memory_store.get_session = AsyncMock(return_value=None)
        session._memory_store.put_session = AsyncMock()
        session._memory_store.put_message = AsyncMock()

        async def empty_iterator() -> AsyncGenerator[None, None]:
            return
            yield

        session._memory_store.list_messages = AsyncMock(
            return_value=empty_iterator(),
        )

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

        # Verify the state was serialized correctly
        saved_message = session._memory_store.put_message.call_args[0][0]
        saved_state = json.loads(saved_message.content)
        self.assertIn("memory", saved_state)
        self.assertIn("content", saved_state["memory"])
