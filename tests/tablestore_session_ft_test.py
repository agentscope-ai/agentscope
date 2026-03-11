# -*- coding: utf-8 -*-
"""Functional tests for TablestoreSession with real Tablestore instance.

These tests require the following environment variables to be set:
    - TABLESTORE_ENDPOINT
    - TABLESTORE_INSTANCE_NAME
    - TABLESTORE_ACCESS_KEY_ID
    - TABLESTORE_ACCESS_KEY_SECRET

If any of these are missing, the tests will be skipped.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import os
import unittest
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.module import StateModule

if TYPE_CHECKING:
    from agentscope.session import TablestoreSession


def _get_tablestore_config() -> dict[str, str] | None:
    """Get Tablestore configuration from environment variables."""
    endpoint = os.getenv("TABLESTORE_ENDPOINT")
    instance_name = os.getenv("TABLESTORE_INSTANCE_NAME")
    access_key_id = os.getenv("TABLESTORE_ACCESS_KEY_ID")
    access_key_secret = os.getenv("TABLESTORE_ACCESS_KEY_SECRET")

    if not all([endpoint, instance_name, access_key_id, access_key_secret]):
        return None

    assert endpoint is not None
    assert instance_name is not None
    assert access_key_id is not None
    assert access_key_secret is not None

    return {
        "end_point": endpoint,
        "instance_name": instance_name,
        "access_key_id": access_key_id,
        "access_key_secret": access_key_secret,
    }


class SimpleStateModule(StateModule):
    """A simple state module for testing."""

    def __init__(self) -> None:
        super().__init__()
        self.name = "test_agent"
        self.value = 42

    def state_dict(self) -> dict:
        return {"name": self.name, "value": self.value}

    def load_state_dict(self, state_dict: dict, strict: bool = True) -> None:
        self.name = state_dict.get("name", self.name)
        self.value = state_dict.get("value", self.value)


@pytest.fixture
def tablestore_config() -> dict[str, str]:
    """Fixture that provides Tablestore config or skips the test."""
    config = _get_tablestore_config()
    if config is None:
        pytest.skip(
            "Tablestore environment variables not set: "
            "TABLESTORE_ENDPOINT, TABLESTORE_INSTANCE_NAME, "
            "TABLESTORE_ACCESS_KEY_ID, TABLESTORE_ACCESS_KEY_SECRET",
        )
    return config  # type: ignore[return-value]


@pytest_asyncio.fixture
async def tablestore_session(  # type: ignore[misc]
    tablestore_config: dict[str, str],
) -> None:
    """Fixture that creates and yields a TablestoreSession, then closes it."""
    from agentscope.session import TablestoreSession

    session = TablestoreSession(
        session_table_name="agentscope_ft_session",
        message_table_name="agentscope_ft_message",
        **tablestore_config,
    )

    async with session as session_instance:
        yield session_instance


@pytest.mark.asyncio
async def test_session_lifecycle(tablestore_config: dict[str, str]) -> None:
    """Test creating and closing a TablestoreSession."""
    from agentscope.session import TablestoreSession

    session = TablestoreSession(
        session_table_name="agentscope_ft_session",
        message_table_name="agentscope_ft_message",
        **tablestore_config,
    )

    await session._ensure_initialized()
    assert session._initialized is True
    assert session._memory_store is not None

    await session.close()
    assert session._initialized is False
    assert session._memory_store is None


@pytest.mark.asyncio
async def test_save_and_load_session_state(
    tablestore_session: TablestoreSession,
) -> None:
    """Test saving and loading session state with a simple state module."""
    session = tablestore_session
    session_id = "ft_test_session_save_load"
    user_id = "ft_test_user"

    # Create and save state
    agent = SimpleStateModule()
    agent.name = "Friday"
    agent.value = 100

    await session.save_session_state(
        session_id=session_id,
        user_id=user_id,
        agent=agent,
    )

    # Load state into a new module
    loaded_agent = SimpleStateModule()
    assert loaded_agent.name == "test_agent"
    assert loaded_agent.value == 42

    await session.load_session_state(
        session_id=session_id,
        user_id=user_id,
        agent=loaded_agent,
    )

    assert loaded_agent.name == "Friday"
    assert loaded_agent.value == 100

    # Cleanup
    await session._memory_store.delete_session(
        user_id=user_id,
        session_id=session_id,
    )


@pytest.mark.asyncio
async def test_save_overwrites_existing_state(
    tablestore_session: TablestoreSession,
) -> None:
    """Test that saving state overwrites the previous state."""
    session = tablestore_session
    session_id = "ft_test_session_overwrite"
    user_id = "ft_test_user"

    # Save initial state
    agent = SimpleStateModule()
    agent.name = "Version1"
    agent.value = 1

    await session.save_session_state(
        session_id=session_id,
        user_id=user_id,
        agent=agent,
    )

    # Save updated state
    agent.name = "Version2"
    agent.value = 2

    await session.save_session_state(
        session_id=session_id,
        user_id=user_id,
        agent=agent,
    )

    # Load and verify the latest state
    loaded_agent = SimpleStateModule()
    await session.load_session_state(
        session_id=session_id,
        user_id=user_id,
        agent=loaded_agent,
    )

    assert loaded_agent.name == "Version2"
    assert loaded_agent.value == 2

    # Cleanup
    await session._memory_store.delete_session(
        user_id=user_id,
        session_id=session_id,
    )


@pytest.mark.asyncio
async def test_load_nonexistent_session_allowed(
    tablestore_session: TablestoreSession,
) -> None:
    """Test loading a non-existent session with allow_not_exist=True."""
    session = tablestore_session

    agent = SimpleStateModule()
    original_name = agent.name
    original_value = agent.value

    # Should not raise, state should remain unchanged
    await session.load_session_state(
        session_id="ft_nonexistent_session_id",
        user_id="ft_nonexistent_user",
        allow_not_exist=True,
        agent=agent,
    )

    assert agent.name == original_name
    assert agent.value == original_value


@pytest.mark.asyncio
async def test_load_nonexistent_session_disallowed(
    tablestore_session: TablestoreSession,
) -> None:
    """Test loading a non-existent session with allow_not_exist=False."""
    session = tablestore_session

    agent = SimpleStateModule()

    with pytest.raises(ValueError):
        await session.load_session_state(
            session_id="ft_nonexistent_session_id_strict",
            user_id="ft_nonexistent_user_strict",
            allow_not_exist=False,
            agent=agent,
        )


@pytest.mark.asyncio
async def test_save_and_load_multiple_modules(
    tablestore_session: TablestoreSession,
) -> None:
    """Test saving and loading multiple state modules in one session."""
    session = tablestore_session
    session_id = "ft_test_session_multi_modules"
    user_id = "ft_test_user"

    # Create multiple modules
    agent1 = SimpleStateModule()
    agent1.name = "Agent1"
    agent1.value = 10

    agent2 = SimpleStateModule()
    agent2.name = "Agent2"
    agent2.value = 20

    await session.save_session_state(
        session_id=session_id,
        user_id=user_id,
        agent1=agent1,
        agent2=agent2,
    )

    # Load into new modules
    loaded1 = SimpleStateModule()
    loaded2 = SimpleStateModule()

    await session.load_session_state(
        session_id=session_id,
        user_id=user_id,
        agent1=loaded1,
        agent2=loaded2,
    )

    assert loaded1.name == "Agent1"
    assert loaded1.value == 10
    assert loaded2.name == "Agent2"
    assert loaded2.value == 20

    # Cleanup
    await session._memory_store.delete_session(
        user_id=user_id,
        session_id=session_id,
    )


@pytest.mark.asyncio
async def test_save_and_load_with_memory_module(
    tablestore_session: TablestoreSession,
) -> None:
    """Test saving and loading a session that includes an InMemoryMemory."""
    session = tablestore_session
    session_id = "ft_test_session_with_memory"
    user_id = "ft_test_user"

    # Create a memory module with messages
    memory = InMemoryMemory()
    await memory.add(Msg("Alice", "Hello!", "user"))
    await memory.add(Msg("Bob", "Hi there!", "assistant"))

    await session.save_session_state(
        session_id=session_id,
        user_id=user_id,
        memory=memory,
    )

    # Load into a new memory module
    loaded_memory = InMemoryMemory()
    await session.load_session_state(
        session_id=session_id,
        user_id=user_id,
        memory=loaded_memory,
    )

    loaded_msgs = await loaded_memory.get_memory()
    assert len(loaded_msgs) == 2
    assert loaded_msgs[0].name == "Alice"
    assert loaded_msgs[0].content == "Hello!"
    assert loaded_msgs[1].name == "Bob"
    assert loaded_msgs[1].content == "Hi there!"

    # Cleanup
    await session._memory_store.delete_session(
        user_id=user_id,
        session_id=session_id,
    )


@pytest.mark.asyncio
async def test_context_manager(tablestore_config: dict[str, str]) -> None:
    """Test using TablestoreSession as an async context manager."""
    from agentscope.session import TablestoreSession

    session = TablestoreSession(
        session_table_name="agentscope_ft_session",
        message_table_name="agentscope_ft_message",
        **tablestore_config,
    )

    async with session as session_instance:
        assert session_instance._initialized is True

    assert session._initialized is False
    assert session._memory_store is None


if __name__ == "__main__":
    unittest.main()
