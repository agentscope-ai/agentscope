# -*- coding: utf-8 -*-
"""Glob tool test case."""
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool._builtin._glob import Glob
from agentscope.tool import ToolChunk, PermissionContext, PermissionBehavior
from agentscope.message import TextBlock


class GlobToolTest(IsolatedAsyncioTestCase):
    """The glob tool test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.glob_tool = Glob()
        # Create a temporary directory with test files
        self.temp_dir = tempfile.mkdtemp()

        # Create test files
        open(os.path.join(self.temp_dir, "test1.py"), 'w').close()
        open(os.path.join(self.temp_dir, "test2.py"), 'w').close()
        open(os.path.join(self.temp_dir, "test.txt"), 'w').close()

        # Create subdirectory
        sub_dir = os.path.join(self.temp_dir, "subdir")
        os.makedirs(sub_dir)
        open(os.path.join(sub_dir, "test3.py"), 'w').close()

    async def asyncTearDown(self) -> None:
        """Clean up temporary files."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    async def test_tool_properties(self) -> None:
        """Test glob tool properties."""
        self.assertEqual(self.glob_tool.name, "glob")
        self.assertIsInstance(self.glob_tool.description, str)
        self.assertIsInstance(self.glob_tool.input_schema, dict)
        self.assertFalse(self.glob_tool.is_mcp)
        self.assertTrue(self.glob_tool.is_read_only)
        self.assertTrue(self.glob_tool.is_concurrency_safe)

    async def test_check_permissions(self) -> None:
        """Test glob tool permission checking."""
        context = PermissionContext()
        tool_input = {"pattern": "*.py"}
        decision = await self.glob_tool.check_permissions(tool_input, context)

        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_simple_pattern(self) -> None:
        """Test simple glob pattern."""
        chunks = []
        async for chunk in self.glob_tool(
            pattern="*.py",
            path=self.temp_dir
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "running")

        # Should find test1.py and test2.py
        content = chunks[0].content[0].text
        self.assertIn("test1.py", content)
        self.assertIn("test2.py", content)
        self.assertNotIn("test.txt", content)

    async def test_recursive_pattern(self) -> None:
        """Test recursive glob pattern."""
        chunks = []
        async for chunk in self.glob_tool(
            pattern="**/*.py",
            path=self.temp_dir
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        content = chunks[0].content[0].text

        # Should find all .py files including in subdirectory
        self.assertIn("test1.py", content)
        self.assertIn("test2.py", content)
        self.assertIn("test3.py", content)

    async def test_no_matches(self) -> None:
        """Test pattern with no matches."""
        chunks = []
        async for chunk in self.glob_tool(
            pattern="*.nonexistent",
            path=self.temp_dir
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "running")
        self.assertIn("No files found", chunks[0].content[0].text)
