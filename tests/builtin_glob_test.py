# -*- coding: utf-8 -*-
"""Glob tool test case."""
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import PermissionContext, PermissionBehavior, Glob


class GlobToolTest(IsolatedAsyncioTestCase):
    """The glob tool test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.glob_tool = Glob()
        # Create a temporary directory with test files
        self.temp_dir = tempfile.mkdtemp()

        # Create test files
        with open(
            os.path.join(self.temp_dir, "test1.py"),
            "w",
            encoding="utf-8",
        ):
            pass
        with open(
            os.path.join(self.temp_dir, "test2.py"),
            "w",
            encoding="utf-8",
        ):
            pass
        with open(
            os.path.join(self.temp_dir, "test.txt"),
            "w",
            encoding="utf-8",
        ):
            pass

        # Create subdirectory
        sub_dir = os.path.join(self.temp_dir, "subdir")
        os.makedirs(sub_dir)
        with open(os.path.join(sub_dir, "test3.py"), "w", encoding="utf-8"):
            pass

    async def asyncTearDown(self) -> None:
        """Clean up temporary files."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    async def test_tool_properties(self) -> None:
        """Test glob tool properties."""
        self.assertEqual(self.glob_tool.name, "Glob")
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

        # Read/Glob/Grep are read-only, return PASSTHROUGH
        self.assertEqual(decision.behavior, PermissionBehavior.PASSTHROUGH)

    async def test_simple_pattern(self) -> None:
        """Test simple glob pattern."""
        chunk = await self.glob_tool(
            pattern="*.py",
            path=self.temp_dir,
        )

        self.assertEqual(chunk.state, "running")

        # Should find test1.py and test2.py
        content = chunk.content[0].text
        self.assertIn("test1.py", content)
        self.assertIn("test2.py", content)
        self.assertNotIn("test.txt", content)

    async def test_recursive_pattern(self) -> None:
        """Test recursive glob pattern."""
        chunk = await self.glob_tool(
            pattern="**/*.py",
            path=self.temp_dir,
        )

        content = chunk.content[0].text

        # Should find all .py files including in subdirectory
        self.assertIn("test1.py", content)
        self.assertIn("test2.py", content)
        self.assertIn("test3.py", content)

    async def test_no_matches(self) -> None:
        """Test pattern with no matches."""
        chunk = await self.glob_tool(
            pattern="*.nonexistent",
            path=self.temp_dir,
        )

        self.assertEqual(chunk.state, "running")
        self.assertIn("No files found", chunk.content[0].text)
