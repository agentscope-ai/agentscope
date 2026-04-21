# -*- coding: utf-8 -*-
"""Grep tool test case."""
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import PermissionContext, PermissionBehavior, Grep


class GrepToolTest(IsolatedAsyncioTestCase):
    """The grep tool test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.grep_tool = Grep()
        # Create a temporary directory with test files
        self.temp_dir = tempfile.mkdtemp()

        # Create test files
        with open(
            os.path.join(self.temp_dir, "test1.py"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("def hello():\n    print('Hello World')\n")

        with open(
            os.path.join(self.temp_dir, "test2.py"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("def goodbye():\n    print('Goodbye')\n")

        with open(
            os.path.join(self.temp_dir, "test.txt"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("This is a text file\nHello from text\n")

        # Create subdirectory with files for glob pattern testing
        subdir = os.path.join(self.temp_dir, "subdir")
        os.makedirs(subdir)
        with open(
            os.path.join(subdir, "nested.py"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("def nested():\n    print('Nested')\n")

    async def asyncTearDown(self) -> None:
        """Clean up temporary files."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    async def test_tool_properties(self) -> None:
        """Test grep tool properties."""
        self.assertEqual(self.grep_tool.name, "Grep")
        self.assertIsInstance(self.grep_tool.description, str)
        self.assertIsInstance(self.grep_tool.input_schema, dict)
        self.assertFalse(self.grep_tool.is_mcp)
        self.assertTrue(self.grep_tool.is_read_only)
        self.assertTrue(self.grep_tool.is_concurrency_safe)

    async def test_check_permissions(self) -> None:
        """Test grep tool permission checking."""
        context = PermissionContext()
        tool_input = {"pattern": "hello"}
        decision = await self.grep_tool.check_permissions(tool_input, context)

        # Read/Glob/Grep are read-only, return PASSTHROUGH
        self.assertEqual(decision.behavior, PermissionBehavior.PASSTHROUGH)

    async def test_simple_search(self) -> None:
        """Test simple grep search."""
        chunks = []
        async for chunk in self.grep_tool(
            pattern="Hello",
            path=self.temp_dir,
            output_mode="files_with_matches",
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "running")

        content = chunks[0].content[0].text
        # Should find files containing "Hello"
        self.assertIn("test1.py", content)
        self.assertIn("test.txt", content)

    async def test_content_mode(self) -> None:
        """Test grep with content output mode."""
        chunks = []
        async for chunk in self.grep_tool(
            pattern="def",
            path=self.temp_dir,
            output_mode="content",
            type="py",
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        content = chunks[0].content[0].text

        # Should show matching lines
        self.assertIn("def hello", content)
        self.assertIn("def goodbye", content)

    async def test_case_insensitive(self) -> None:
        """Test case-insensitive search."""
        chunks = []
        async for chunk in self.grep_tool(
            pattern="HELLO",
            path=self.temp_dir,
            case_insensitive=True,
            output_mode="files_with_matches",
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        content = chunks[0].content[0].text
        self.assertIn("test1.py", content)

    async def test_no_matches(self) -> None:
        """Test search with no matches."""
        chunks = []
        async for chunk in self.grep_tool(
            pattern="NonExistentPattern",
            path=self.temp_dir,
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertIn("No matches found", chunks[0].content[0].text)

    async def test_type_filter(self) -> None:
        """Test filtering by file type."""
        chunks = []
        async for chunk in self.grep_tool(
            pattern="Hello",
            path=self.temp_dir,
            type="py",
            output_mode="files_with_matches",
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        content = chunks[0].content[0].text

        # Should only find .py files
        self.assertIn("test1.py", content)
        self.assertNotIn("test.txt", content)

    async def test_invalid_regex(self) -> None:
        """Test grep with invalid regex pattern."""
        chunks = []
        async for chunk in self.grep_tool(
            pattern="[invalid(regex",
            path=self.temp_dir,
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "error")
        self.assertIn("Invalid regex pattern", chunks[0].content[0].text)

    async def test_glob_pattern_with_subdirs(self) -> None:
        """Test glob pattern matching with subdirectories like **/*.py."""
        chunks = []
        async for chunk in self.grep_tool(
            pattern="def",
            path=self.temp_dir,
            glob="**/*.py",
            output_mode="files_with_matches",
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        content = chunks[0].content[0].text

        # Should find all .py files including in subdirectories
        self.assertIn("test1.py", content)
        self.assertIn("test2.py", content)
        self.assertIn("nested.py", content)
        # Should not find .txt files
        self.assertNotIn("test.txt", content)
