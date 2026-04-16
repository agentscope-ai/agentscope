# -*- coding: utf-8 -*-
"""Functional tests for TablestoreMemory with real Tablestore instance.

These tests require the following environment variables to be set:
    - TABLESTORE_ENDPOINT
    - TABLESTORE_INSTANCE_NAME
    - TABLESTORE_ACCESS_KEY_ID
    - TABLESTORE_ACCESS_KEY_SECRET

If any of these are missing, the tests will be skipped.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from agentscope.message import Msg

if TYPE_CHECKING:
    from agentscope.memory import TablestoreMemory


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


async def _wait_for_index_ready(
    memory: "TablestoreMemory",
    expected_count: int,
) -> None:
    """Wait for the search index to be ready with the expected document count.

    Uses TablestoreHelper to poll the search index until the total count
    matches the expected count.
    """
    from tablestore_for_agent_memory.util.tablestore_helper import (
        TablestoreHelper,
    )

    await memory._ensure_initialized()
    tablestore_client = memory._tablestore_client
    table_name = memory._knowledge_store._table_name
    index_name = memory._knowledge_store._search_index_name

    await TablestoreHelper.async_wait_search_index_ready(
        tablestore_client=tablestore_client,
        table_name=table_name,
        index_name=index_name,
        total_count=expected_count,
    )


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
async def tablestore_memory(  # type: ignore[misc]
    tablestore_config: dict[str, str],
) -> None:
    """Fixture that creates a TablestoreMemory for testing."""
    from agentscope.memory import TablestoreMemory

    memory = TablestoreMemory(
        user_id="ft_test_user",
        session_id="ft_test_session",
        table_name="agentscope_ft_memory",
        vector_dimension=4,
        **tablestore_config,
    )

    await memory._ensure_initialized()

    # Clean up any existing data before test
    await memory.clear()
    await _wait_for_index_ready(memory, 0)

    try:
        yield memory
    finally:
        # Clean up after test
        await memory.clear()
        await _wait_for_index_ready(memory, 0)
        await memory.close()


@pytest.mark.asyncio
async def test_memory_lifecycle(tablestore_config: dict[str, str]) -> None:
    """Test creating and closing a TablestoreMemory."""
    from agentscope.memory import TablestoreMemory

    memory = TablestoreMemory(
        user_id="ft_lifecycle_user",
        session_id="ft_lifecycle_session",
        table_name="agentscope_ft_memory",
        vector_dimension=4,
        **tablestore_config,
    )

    await memory._ensure_initialized()
    assert memory._initialized is True
    assert memory._knowledge_store is not None

    await memory.close()
    assert memory._initialized is False
    assert memory._knowledge_store is None


@pytest.mark.asyncio
async def test_add_and_get_memory(
    tablestore_memory: TablestoreMemory,
) -> None:
    """Test adding messages and retrieving them."""
    memory = tablestore_memory

    msg1 = Msg("Alice", "Hello world!", "user")
    msg2 = Msg("Bob", "Hi there!", "assistant")

    await memory.add(msg1)
    await memory.add(msg2)
    await _wait_for_index_ready(memory, 2)

    messages = await memory.get_memory()
    assert len(messages) == 2

    names = {m.name for m in messages}
    assert "Alice" in names
    assert "Bob" in names


@pytest.mark.asyncio
async def test_add_multiple_messages(
    tablestore_memory: TablestoreMemory,
) -> None:
    """Test adding a list of messages at once."""
    memory = tablestore_memory

    msgs = [Msg("User", f"Message {i}", "user") for i in range(5)]

    await memory.add(msgs)
    await _wait_for_index_ready(memory, 5)

    result = await memory.get_memory()
    assert len(result) == 5


@pytest.mark.asyncio
async def test_add_no_duplicates(
    tablestore_memory: TablestoreMemory,
) -> None:
    """Test duplicate messages not added when allow_duplicates=False."""
    memory = tablestore_memory

    msg = Msg("Alice", "Hello!", "user")

    await memory.add(msg)
    await _wait_for_index_ready(memory, 1)

    # Try to add the same message again
    await memory.add(msg, allow_duplicates=False)
    await _wait_for_index_ready(memory, 1)

    result = await memory.get_memory()
    assert len(result) == 1


@pytest.mark.asyncio
async def test_add_allow_duplicates(
    tablestore_memory: TablestoreMemory,
) -> None:
    """Test that duplicate messages are added when allow_duplicates=True."""
    memory = tablestore_memory

    msg = Msg("Alice", "Hello!", "user")

    await memory.add(msg)
    await _wait_for_index_ready(memory, 1)

    # Add the same message again with allow_duplicates=True
    # Since the same msg.id is used as document_id, Tablestore will
    # upsert (overwrite) the existing row, so count stays at 1.
    await memory.add(msg, allow_duplicates=True)
    await _wait_for_index_ready(memory, 1)

    result = await memory.get_memory()
    assert len(result) == 1


@pytest.mark.asyncio
async def test_delete_messages(tablestore_memory: TablestoreMemory) -> None:
    """Test deleting messages by ID."""
    memory = tablestore_memory

    msg1 = Msg("Alice", "Hello!", "user")
    msg2 = Msg("Bob", "Hi!", "assistant")

    await memory.add([msg1, msg2])
    await _wait_for_index_ready(memory, 2)

    # Delete msg1
    deleted = await memory.delete([msg1.id])
    assert deleted == 1
    await _wait_for_index_ready(memory, 1)

    result = await memory.get_memory()
    assert len(result) == 1
    assert result[0].name == "Bob"


@pytest.mark.asyncio
async def test_delete_nonexistent(
    tablestore_memory: TablestoreMemory,
) -> None:
    """Test deleting a non-existent message does not raise error."""
    memory = tablestore_memory

    deleted = await memory.delete(["nonexistent_id"])
    assert deleted == 0


@pytest.mark.asyncio
async def test_size(tablestore_memory: TablestoreMemory) -> None:
    """Test getting the size of memory."""
    memory = tablestore_memory

    assert await memory.size() == 0

    msgs = [Msg("User", f"Msg {i}", "user") for i in range(3)]
    await memory.add(msgs)
    await _wait_for_index_ready(memory, 3)

    assert await memory.size() == 3


@pytest.mark.asyncio
async def test_clear(tablestore_memory: TablestoreMemory) -> None:
    """Test clearing all messages."""
    memory = tablestore_memory

    msgs = [Msg("User", f"Msg {i}", "user") for i in range(5)]
    await memory.add(msgs)
    await _wait_for_index_ready(memory, 5)

    assert await memory.size() == 5

    await memory.clear()
    await _wait_for_index_ready(memory, 0)

    assert await memory.size() == 0


@pytest.mark.asyncio
async def test_add_with_marks(tablestore_memory: TablestoreMemory) -> None:
    """Test adding messages with marks."""
    memory = tablestore_memory

    msg = Msg("Alice", "Important message", "user")
    await memory.add(msg, marks=["important", "review"])
    await _wait_for_index_ready(memory, 1)

    # Get all messages
    all_msgs = await memory.get_memory()
    assert len(all_msgs) == 1

    # Get messages with specific mark
    important_msgs = await memory.get_memory(mark="important")
    assert len(important_msgs) == 1

    # Get messages with non-matching mark
    other_msgs = await memory.get_memory(mark="other")
    assert len(other_msgs) == 0


@pytest.mark.asyncio
async def test_get_memory_with_mark_filter(
    tablestore_memory: TablestoreMemory,
) -> None:
    """Test filtering messages by mark."""
    memory = tablestore_memory

    msg1 = Msg("Alice", "Important", "user")
    msg2 = Msg("Bob", "Normal", "assistant")
    msg3 = Msg("Charlie", "Also important", "user")

    await memory.add(msg1, marks=["important"])
    await memory.add(msg2, marks=["normal"])
    await memory.add(msg3, marks=["important"])
    await _wait_for_index_ready(memory, 3)

    important_msgs = await memory.get_memory(mark="important")
    assert len(important_msgs) == 2

    normal_msgs = await memory.get_memory(mark="normal")
    assert len(normal_msgs) == 1
    assert normal_msgs[0].name == "Bob"


@pytest.mark.asyncio
async def test_get_memory_with_exclude_mark(
    tablestore_memory: TablestoreMemory,
) -> None:
    """Test excluding messages by mark."""
    memory = tablestore_memory

    msg1 = Msg("Alice", "Keep me", "user")
    msg2 = Msg("Bob", "Exclude me", "assistant")

    await memory.add(msg1, marks=["keep"])
    await memory.add(msg2, marks=["exclude"])
    await _wait_for_index_ready(memory, 2)

    result = await memory.get_memory(exclude_mark="exclude")
    assert len(result) == 1
    assert result[0].name == "Alice"


@pytest.mark.asyncio
async def test_delete_by_mark(tablestore_memory: TablestoreMemory) -> None:
    """Test deleting messages by mark."""
    memory = tablestore_memory

    msg1 = Msg("Alice", "Keep me", "user")
    msg2 = Msg("Bob", "Delete me", "assistant")
    msg3 = Msg("Charlie", "Delete me too", "user")

    await memory.add(msg1, marks=["keep"])
    await memory.add(msg2, marks=["delete"])
    await memory.add(msg3, marks=["delete"])
    await _wait_for_index_ready(memory, 3)

    deleted = await memory.delete_by_mark("delete")
    assert deleted == 2
    await _wait_for_index_ready(memory, 1)

    result = await memory.get_memory()
    assert len(result) == 1
    assert result[0].name == "Alice"


@pytest.mark.asyncio
async def test_update_messages_mark_add(
    tablestore_memory: TablestoreMemory,
) -> None:
    """Test adding a mark to messages."""
    memory = tablestore_memory

    msg1 = Msg("Alice", "Hello", "user")
    msg2 = Msg("Bob", "Hi", "assistant")

    await memory.add([msg1, msg2])
    await _wait_for_index_ready(memory, 2)

    updated = await memory.update_messages_mark(
        msg_ids=[msg1.id],
        new_mark="reviewed",
    )
    assert updated == 1

    # Wait for search index to sync metadata update (update_row needs time)
    await asyncio.sleep(20)

    # Verify the mark was added
    reviewed_msgs = await memory.get_memory(mark="reviewed")
    assert len(reviewed_msgs) == 1
    assert reviewed_msgs[0].name == "Alice"


@pytest.mark.asyncio
async def test_update_messages_mark_replace(
    tablestore_memory: TablestoreMemory,
) -> None:
    """Test replacing a mark on messages."""
    memory = tablestore_memory

    msg = Msg("Alice", "Hello", "user")
    await memory.add(msg, marks=["draft"])
    await _wait_for_index_ready(memory, 1)

    updated = await memory.update_messages_mark(
        msg_ids=[msg.id],
        old_mark="draft",
        new_mark="final",
    )
    assert updated == 1

    # Wait for search index to sync metadata update (update_row needs time)
    await asyncio.sleep(20)

    # Old mark should not match
    draft_msgs = await memory.get_memory(mark="draft")
    assert len(draft_msgs) == 0

    # New mark should match
    final_msgs = await memory.get_memory(mark="final")
    assert len(final_msgs) == 1


@pytest.mark.asyncio
async def test_update_messages_mark_remove(
    tablestore_memory: TablestoreMemory,
) -> None:
    """Test removing a mark from messages."""
    memory = tablestore_memory

    msg = Msg("Alice", "Hello", "user")
    await memory.add(msg, marks=["temporary"])
    await _wait_for_index_ready(memory, 1)

    updated = await memory.update_messages_mark(
        msg_ids=[msg.id],
        old_mark="temporary",
        new_mark=None,
    )
    assert updated == 1

    # Wait for search index to sync metadata update (update_row needs time)
    await asyncio.sleep(20)

    # Mark should be removed
    temp_msgs = await memory.get_memory(mark="temporary")
    assert len(temp_msgs) == 0

    # Message should still exist
    all_msgs = await memory.get_memory()
    assert len(all_msgs) == 1


@pytest.mark.asyncio
async def test_compressed_summary(
    tablestore_memory: TablestoreMemory,
) -> None:
    """Test compressed summary functionality."""
    memory = tablestore_memory

    msg = Msg("Alice", "Hello", "user")
    await memory.add(msg)
    await _wait_for_index_ready(memory, 1)

    # Set compressed summary
    await memory.update_compressed_summary("This is a summary of the chat.")

    # Get memory with summary prepended
    result = await memory.get_memory(prepend_summary=True)
    assert len(result) == 2
    assert result[0].content == "This is a summary of the chat."
    assert result[1].name == "Alice"

    # Get memory without summary
    result_no_summary = await memory.get_memory(prepend_summary=False)
    assert len(result_no_summary) == 1


@pytest.mark.asyncio
async def test_state_dict_roundtrip(
    tablestore_memory: TablestoreMemory,
) -> None:
    """Test state_dict and load_state_dict roundtrip."""
    memory = tablestore_memory

    await memory.update_compressed_summary("Test summary for state dict")

    state = memory.state_dict()
    assert state["_compressed_summary"] == "Test summary for state dict"

    # Create a new memory and load state
    from agentscope.memory import TablestoreMemory

    config = _get_tablestore_config()
    new_memory = TablestoreMemory(
        user_id="ft_state_user",
        session_id="ft_state_session",
        table_name="agentscope_ft_memory",
        vector_dimension=4,
        **config,
    )
    new_memory.load_state_dict(state)

    assert new_memory._compressed_summary == "Test summary for state dict"
    await new_memory.close()


@pytest.mark.asyncio
async def test_msg_roundtrip(tablestore_memory: TablestoreMemory) -> None:
    """Test that Msg content is preserved through add/get roundtrip."""
    memory = tablestore_memory

    original_msg = Msg(
        "Alice",
        "Hello with special chars: 你好世界 & <tag>",
        "user",
        metadata={"key": "value", "number": 42},
    )

    await memory.clear()
    await memory.add(original_msg)
    await _wait_for_index_ready(memory, 1)

    result = await memory.get_memory()
    assert len(result) == 1

    restored = result[0]
    assert restored.name == "Alice"
    assert restored.content == "Hello with special chars: 你好世界 & <tag>"
    assert restored.role == "user"
    assert restored.id == original_msg.id


@pytest.mark.asyncio
async def test_isolation_between_users(
    tablestore_config: dict[str, str],
) -> None:
    """Test that different user_id/session_id combinations are isolated."""
    from agentscope.memory import TablestoreMemory

    memory1 = TablestoreMemory(
        user_id="ft_user_A",
        session_id="ft_session_1",
        table_name="agentscope_ft_memory",
        vector_dimension=4,
        **tablestore_config,
    )
    memory2 = TablestoreMemory(
        user_id="ft_user_B",
        session_id="ft_session_1",
        table_name="agentscope_ft_memory",
        vector_dimension=4,
        **tablestore_config,
    )

    try:
        await memory1._ensure_initialized()
        await memory2._ensure_initialized()

        # Clean up
        await memory1.clear()
        await memory2.clear()
        await _wait_for_index_ready(memory1, 0)
        await _wait_for_index_ready(memory2, 0)

        # Add messages to memory1
        await memory1.add(Msg("Alice", "Hello from user A", "user"))
        await _wait_for_index_ready(memory1, 1)

        # memory2 should not see memory1's messages
        result2 = await memory2.get_memory()
        assert len(result2) == 0

        # memory1 should see its own messages
        result1 = await memory1.get_memory()
        assert len(result1) == 1
        assert result1[0].name == "Alice"

    finally:
        await memory1.clear()
        await memory2.clear()
        await memory1.close()
        await memory2.close()


@pytest.mark.asyncio
async def test_add_none(tablestore_memory: TablestoreMemory) -> None:
    """Test that adding None does nothing."""
    memory = tablestore_memory

    await memory.add(None)

    assert await memory.size() == 0


@pytest.mark.asyncio
async def test_get_memory_preserves_insertion_order(
    tablestore_memory: TablestoreMemory,
) -> None:
    """Test that get_memory returns messages sorted by timestamp,
    preserving the insertion order."""
    memory = tablestore_memory

    msg1 = Msg(
        "Alice",
        "First message",
        "user",
        timestamp="2026-01-01 00:00:01.000",
    )
    msg2 = Msg(
        "Bob",
        "Second message",
        "assistant",
        timestamp="2026-01-01 00:00:02.000",
    )
    msg3 = Msg(
        "Charlie",
        "Third message",
        "user",
        timestamp="2026-01-01 00:00:03.000",
    )

    # Insert in reverse order to ensure sorting is by timestamp,
    # not by insertion order in Tablestore
    await memory.add(msg3)
    await memory.add(msg1)
    await memory.add(msg2)
    await _wait_for_index_ready(memory, 3)

    messages = await memory.get_memory()
    assert len(messages) == 3

    assert messages[0].name == "Alice"
    assert messages[1].name == "Bob"
    assert messages[2].name == "Charlie"

    assert messages[0].timestamp == "2026-01-01 00:00:01.000"
    assert messages[1].timestamp == "2026-01-01 00:00:02.000"
    assert messages[2].timestamp == "2026-01-01 00:00:03.000"
