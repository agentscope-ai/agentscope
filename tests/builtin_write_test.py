# -*- coding: utf-8 -*-
"""Write tool test case."""
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import PermissionContext, PermissionBehavior, Write


class WriteToolTest(IsolatedAsyncioTestCase):
    """The write tool test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.write_tool = Write()
        self.temp_dir = tempfile.mkdtemp()

    async def asyncTearDown(self) -> None:
        """Clean up temporary files."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    async def test_tool_properties(self) -> None:
        """Test write tool properties."""
        self.assertEqual(self.write_tool.name, "write")
        self.assertIsInstance(self.write_tool.description, str)
        self.assertIsInstance(self.write_tool.input_schema, dict)
        self.assertFalse(self.write_tool.is_mcp)
        self.assertFalse(self.write_tool.is_read_only)
        self.assertFalse(self.write_tool.is_concurrency_safe)

    async def test_check_permissions(self) -> None:
        """Test write tool permission checking."""
        context = PermissionContext()
        tool_input = {"file_path": "/tmp/test.txt"}
        decision = await self.write_tool.check_permissions(tool_input, context)

        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("/tmp/test.txt", decision.message)

    async def test_simple_write(self) -> None:
        """Test simple file writing."""
        file_path = os.path.join(self.temp_dir, "test.txt")
        content = "Hello World\nThis is a test\n"

        chunks = []
        async for chunk in self.write_tool(
            file_path=file_path,
            content=content,
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "running")
        self.assertTrue(chunks[0].is_last)

        # Verify file was created and content is correct
        self.assertTrue(os.path.exists(file_path))
        with open(file_path, "r", encoding="utf-8") as f:
            written_content = f.read()
        self.assertEqual(written_content, content)

    async def test_write_creates_directory(self) -> None:
        """Test that write creates parent directories."""
        file_path = os.path.join(self.temp_dir, "subdir", "test.txt")
        content = "Test content"

        chunks = []
        async for chunk in self.write_tool(
            file_path=file_path,
            content=content,
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "running")

        # Verify directory and file were created
        self.assertTrue(os.path.exists(file_path))
        with open(file_path, "r", encoding="utf-8") as f:
            written_content = f.read()
        self.assertEqual(written_content, content)

    async def test_write_overwrites_existing(self) -> None:
        """Test that write overwrites existing files."""
        file_path = os.path.join(self.temp_dir, "test.txt")

        # Write initial content
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("Initial content")

        # Overwrite with new content
        new_content = "New content"
        chunks = []
        async for chunk in self.write_tool(
            file_path=file_path,
            content=new_content,
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)

        # Verify content was overwritten
        with open(file_path, "r", encoding="utf-8") as f:
            written_content = f.read()
        self.assertEqual(written_content, new_content)
        self.assertNotIn("Initial", written_content)

    async def test_write_empty_content(self) -> None:
        """Test writing empty content."""
        file_path = os.path.join(self.temp_dir, "empty.txt")

        chunks = []
        async for chunk in self.write_tool(
            file_path=file_path,
            content="",
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertTrue(os.path.exists(file_path))

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "")
