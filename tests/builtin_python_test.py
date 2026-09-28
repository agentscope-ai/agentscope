# -*- coding: utf-8 -*-
"""Python tool test case."""

from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import TextBlock
from agentscope.tool import Python, ToolChunk
from agentscope.permission import PermissionContext, PermissionBehavior


class PythonToolTest(IsolatedAsyncioTestCase):
    """The Python tool test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.python_tool = Python()

    async def test_tool_properties(self) -> None:
        """Test Python tool properties."""
        self.assertEqual(self.python_tool.name, "Python")
        self.assertIsInstance(self.python_tool.description, str)
        self.assertIsInstance(self.python_tool.input_schema, dict)
        self.assertFalse(self.python_tool.is_mcp)
        self.assertFalse(self.python_tool.is_read_only)
        self.assertFalse(self.python_tool.is_concurrency_safe)

    async def test_check_permissions(self) -> None:
        """Test Python tool permission checking."""
        context = PermissionContext()
        tool_input = {"code": "print('hello')"}
        decision = await self.python_tool.check_permissions(
            tool_input,
            context,
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn(
            "Permission required to execute Python code",
            decision.message,
        )

    async def test_simple_code(self) -> None:
        """Test executing simple Python code."""
        chunks = []
        async for chunk in await self.python_tool(code="print('Hello World')"):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertIsInstance(chunks[0], ToolChunk)
        self.assertEqual(chunks[0].state, "running")
        self.assertTrue(chunks[0].is_last)
        self.assertEqual(len(chunks[0].content), 1)
        self.assertIsInstance(chunks[0].content[0], TextBlock)
        self.assertIn("Hello World", chunks[0].content[0].text)

    async def test_code_with_error(self) -> None:
        """Test executing code that fails."""
        chunks = []
        async for chunk in await self.python_tool(code="1 / 0"):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "error")
        self.assertTrue(chunks[0].is_last)
        self.assertIn("ZeroDivisionError", chunks[0].content[0].text)

    async def test_timeout(self) -> None:
        """Test timeout of python execution."""
        chunks = []
        async for chunk in await self.python_tool(
            code="import time\ntime.sleep(1)",
            timeout=0.1,
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "error")
        self.assertIn("timed out", chunks[0].content[0].text.lower())
