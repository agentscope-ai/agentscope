# -*- coding: utf-8 -*-
"""Read tool test case."""
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import (
    ToolChunk,
    PermissionContext,
    PermissionBehavior,
    Read,
)
from agentscope.message import TextBlock


class ReadToolTest(IsolatedAsyncioTestCase):
    """The read tool test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.read_tool = Read()
        # Create a temporary file for testing
        self.temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            suffix=".txt",
        )
        # Write multiple lines
        for i in range(1, 11):
            self.temp_file.write(f"Line {i}\n")
        self.temp_file.close()

    async def asyncTearDown(self) -> None:
        """Clean up temporary files."""
        if os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)

    async def test_tool_properties(self) -> None:
        """Test read tool properties."""
        self.assertEqual(self.read_tool.name, "Read")
        self.assertIsInstance(self.read_tool.description, str)
        self.assertIsInstance(self.read_tool.input_schema, dict)
        self.assertFalse(self.read_tool.is_mcp)
        self.assertTrue(self.read_tool.is_read_only)
        self.assertTrue(self.read_tool.is_concurrency_safe)

    async def test_check_permissions(self) -> None:
        """Test read tool permission checking."""
        context = PermissionContext()
        tool_input = {"file_path": "/tmp/test.txt"}
        decision = await self.read_tool.check_permissions(tool_input, context)

        # Read/Glob/Grep are read-only, return PASSTHROUGH
        self.assertEqual(decision.behavior, PermissionBehavior.PASSTHROUGH)

    async def test_simple_read(self) -> None:
        """Test simple file reading."""
        chunks = []
        async for chunk in self.read_tool(file_path=self.temp_file.name):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertIsInstance(chunks[0], ToolChunk)
        self.assertEqual(chunks[0].state, "running")
        self.assertEqual(len(chunks[0].content), 1)
        self.assertIsInstance(chunks[0].content[0], TextBlock)

        content = chunks[0].content[0].text
        # Should contain all lines with line numbers
        self.assertIn("Line 1", content)
        self.assertIn("Line 10", content)

    async def test_read_with_offset(self) -> None:
        """Test reading with offset."""
        chunks = []
        async for chunk in self.read_tool(
            file_path=self.temp_file.name,
            offset=5,
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "running")
        content = chunks[0].content[0].text

        # Should start from line 5
        self.assertIn("Line 5", content)
        # Line 1 should not appear (but Line 10 contains "1",
        # so check more specifically)
        lines = content.split("\n")
        line_numbers = [
            int(line.split("\t")[0].strip()) for line in lines if line.strip()
        ]
        self.assertNotIn(1, line_numbers)
        self.assertIn(5, line_numbers)

    async def test_read_with_limit(self) -> None:
        """Test reading with limit."""
        chunks = []
        async for chunk in self.read_tool(
            file_path=self.temp_file.name,
            offset=1,
            limit=3,
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "running")
        content = chunks[0].content[0].text

        # Should only read 3 lines
        self.assertIn("Line 1", content)
        self.assertIn("Line 2", content)
        self.assertIn("Line 3", content)
        self.assertNotIn("Line 4", content)

    async def test_read_nonexistent_file(self) -> None:
        """Test reading a non-existent file."""
        chunks = []
        async for chunk in self.read_tool(file_path="/nonexistent/file.txt"):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "error")
        self.assertIn("does not exist", chunks[0].content[0].text)

    async def test_read_directory(self) -> None:
        """Test reading a directory (should fail)."""
        temp_dir = tempfile.mkdtemp()
        try:
            chunks = []
            async for chunk in self.read_tool(file_path=temp_dir):
                chunks.append(chunk)

            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0].state, "error")
            self.assertIn("directory", chunks[0].content[0].text.lower())
        finally:
            os.rmdir(temp_dir)
