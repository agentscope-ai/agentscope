# -*- coding: utf-8 -*-
"""Test cases for the MemoryTool."""
# pylint: disable=protected-access
import time
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.state import AgentState
from agentscope.tool._builtin._memory import MemoryTool
from agentscope.message import TextBlock


class MemoryToolTest(IsolatedAsyncioTestCase):
    """Test cases for the MemoryTool."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.tool = MemoryTool()
        self.state = AgentState(session_id="test-session")

    async def test_save_new_memory(self) -> None:
        """Test saving a new memory entry."""
        result = await self.tool(
            _agent_state=self.state,
            action="save",
            key="test-key",
            value="test-value",
        )
        self.assertIsInstance(result.content[0], TextBlock)
        self.assertIn("Saved", result.content[0].text)
        self.assertIn("test-key", result.content[0].text)
        self.assertIn("test-key", self.state.memory_context.entries)
        self.assertEqual(
            self.state.memory_context.entries["test-key"].value,
            "test-value",
        )

    async def test_save_update_existing_memory(self) -> None:
        """Test updating an existing memory entry."""
        await self.tool(
            _agent_state=self.state,
            action="save",
            key="test-key",
            value="old-value",
        )
        result = await self.tool(
            _agent_state=self.state,
            action="save",
            key="test-key",
            value="new-value",
        )
        self.assertIn("Updated", result.content[0].text)
        self.assertEqual(
            self.state.memory_context.entries["test-key"].value,
            "new-value",
        )

    async def test_save_missing_key(self) -> None:
        """Test save with missing key."""
        result = await self.tool(
            _agent_state=self.state,
            action="save",
            value="some-value",
        )
        self.assertIn("Error", result.content[0].text)
        self.assertIn("key", result.content[0].text)

    async def test_save_missing_value(self) -> None:
        """Test save with missing value."""
        result = await self.tool(
            _agent_state=self.state,
            action="save",
            key="test-key",
        )
        self.assertIn("Error", result.content[0].text)
        self.assertIn("value", result.content[0].text)

    async def test_retrieve_existing_memory(self) -> None:
        """Test retrieving an existing memory."""
        await self.tool(
            _agent_state=self.state,
            action="save",
            key="test-key",
            value="test-value",
        )
        result = await self.tool(
            _agent_state=self.state,
            action="retrieve",
            key="test-key",
        )
        self.assertIn("test-key", result.content[0].text)
        self.assertIn("test-value", result.content[0].text)

    async def test_retrieve_nonexistent_memory(self) -> None:
        """Test retrieving a non-existent memory."""
        result = await self.tool(
            _agent_state=self.state,
            action="retrieve",
            key="nonexistent",
        )
        self.assertIn("No memory found", result.content[0].text)

    async def test_retrieve_all_empty(self) -> None:
        """Test listing all memories when none exist."""
        result = await self.tool(
            _agent_state=self.state,
            action="retrieve",
        )
        self.assertIn("No memories stored", result.content[0].text)

    async def test_retrieve_all(self) -> None:
        """Test listing all memories."""
        await self.tool(
            _agent_state=self.state,
            action="save",
            key="key-a",
            value="value-a",
        )
        await self.tool(
            _agent_state=self.state,
            action="save",
            key="key-b",
            value="value-b",
        )
        result = await self.tool(
            _agent_state=self.state,
            action="retrieve",
        )
        text = result.content[0].text
        self.assertIn("key-a", text)
        self.assertIn("key-b", text)

    async def test_delete_existing_memory(self) -> None:
        """Test deleting an existing memory."""
        await self.tool(
            _agent_state=self.state,
            action="save",
            key="test-key",
            value="test-value",
        )
        result = await self.tool(
            _agent_state=self.state,
            action="delete",
            key="test-key",
        )
        self.assertIn("Deleted", result.content[0].text)
        self.assertNotIn("test-key", self.state.memory_context.entries)

    async def test_delete_nonexistent_memory(self) -> None:
        """Test deleting a non-existent memory."""
        result = await self.tool(
            _agent_state=self.state,
            action="delete",
            key="nonexistent",
        )
        self.assertIn("No memory found", result.content[0].text)

    async def test_delete_missing_key(self) -> None:
        """Test delete with missing key."""
        result = await self.tool(
            _agent_state=self.state,
            action="delete",
        )
        self.assertIn("Error", result.content[0].text)
        self.assertIn("key", result.content[0].text)

    async def test_unknown_action(self) -> None:
        """Test an unknown action."""
        result = await self.tool(
            _agent_state=self.state,
            action="unknown",
        )
        self.assertIn("Unknown action", result.content[0].text)

    async def test_max_entries_eviction(self) -> None:
        """Test that old entries are evicted when max_entries is reached."""
        self.state.memory_context.max_entries = 3
        # Fill up to capacity
        for i in range(3):
            await self.tool(
                _agent_state=self.state,
                action="save",
                key=f"key-{i}",
                value=f"value-{i}",
            )
            time.sleep(0.01)  # Ensure different timestamps

        self.assertEqual(len(self.state.memory_context.entries), 3)

        # Add one more, should evict the oldest
        await self.tool(
            _agent_state=self.state,
            action="save",
            key="key-new",
            value="value-new",
        )
        self.assertEqual(len(self.state.memory_context.entries), 3)
        self.assertIn("key-new", self.state.memory_context.entries)
        # key-0 should be evicted (oldest)
        self.assertNotIn("key-0", self.state.memory_context.entries)

    async def test_input_schema(self) -> None:
        """Test that the input schema is valid."""
        schema = self.tool.input_schema
        self.assertEqual(schema["type"], "object")
        self.assertIn("action", schema["properties"])
        self.assertIn("key", schema["properties"])
        self.assertIn("value", schema["properties"])
        self.assertEqual(
            schema["properties"]["action"]["enum"],
            ["save", "retrieve", "delete"],
        )

    async def test_auto_inject_into_model_input(self) -> None:
        """Test that memories are auto-injected as a UserMsg."""
        from agentscope.agent import Agent
        from agentscope.tool import Toolkit
        from tests.utils import MockModel

        state = AgentState(session_id="test-inject")
        state.memory_context.set("key-a", "value-a", time.time())
        state.memory_context.set(
            "key-b",
            "long-" + "x" * 200,
            time.time(),
        )

        agent = Agent(
            name="Test",
            system_prompt="You are a test agent.",
            model=MockModel(),
            toolkit=Toolkit(),
            state=state,
        )

        result = await agent._prepare_model_input()
        messages = result["messages"]

        mem_msg = None
        for msg in messages:
            content_str = (
                msg.content
                if isinstance(msg.content, str)
                else " ".join(
                    b.text
                    for b in msg.content
                    if hasattr(b, "text")
                )
            )
            if "<agent-memories>" in content_str:
                mem_msg = content_str
                break

        self.assertIsNotNone(mem_msg)
        self.assertIn("key-a", mem_msg)
        self.assertIn("value-a", mem_msg)
        self.assertIn("key-b", mem_msg)
        self.assertIn("x" * 200, mem_msg)
