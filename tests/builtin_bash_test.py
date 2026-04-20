# -*- coding: utf-8 -*-
"""Bash tool test case."""
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import ToolChunk, PermissionContext, Bash
from agentscope.message import TextBlock


class BashToolTest(IsolatedAsyncioTestCase):
    """The bash tool test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.bash_tool = Bash()

    async def test_tool_properties(self) -> None:
        """Test bash tool properties."""
        self.assertEqual(self.bash_tool.name, "bash")
        self.assertIsInstance(self.bash_tool.description, str)
        self.assertIsInstance(self.bash_tool.input_schema, dict)
        self.assertFalse(self.bash_tool.is_mcp)
        self.assertFalse(self.bash_tool.is_read_only)
        self.assertFalse(self.bash_tool.is_concurrency_safe)

    async def test_check_permissions(self) -> None:
        """Test bash tool permission checking."""
        from agentscope.tool import PermissionBehavior

        context = PermissionContext()
        tool_input = {"command": "echo hello"}
        decision = await self.bash_tool.check_permissions(tool_input, context)

        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("echo hello", decision.message)

    async def test_simple_command(self) -> None:
        """Test executing a simple bash command."""
        chunks = []
        async for chunk in self.bash_tool(command="echo 'Hello World'"):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertIsInstance(chunks[0], ToolChunk)
        self.assertEqual(chunks[0].state, "running")
        self.assertTrue(chunks[0].is_last)
        self.assertEqual(len(chunks[0].content), 1)
        self.assertIsInstance(chunks[0].content[0], TextBlock)
        self.assertIn("Hello World", chunks[0].content[0].text)

    async def test_command_with_error(self) -> None:
        """Test executing a command that fails."""
        chunks = []
        async for chunk in self.bash_tool(command="exit 1"):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "error")
        self.assertTrue(chunks[0].is_last)

    async def test_command_timeout(self) -> None:
        """Test command timeout."""
        chunks = []
        async for chunk in self.bash_tool(
            command="sleep 10",
            timeout=100,  # 100ms timeout
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "error")
        self.assertIn("timed out", chunks[0].content[0].text.lower())
