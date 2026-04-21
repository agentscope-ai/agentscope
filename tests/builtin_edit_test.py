# -*- coding: utf-8 -*-
"""Edit tool test case."""
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import PermissionContext, PermissionBehavior, Edit


class EditToolTest(IsolatedAsyncioTestCase):
    """The edit tool test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.edit_tool = Edit()
        # Create a temporary file for testing
        self.temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            suffix=".txt",
        )
        self.temp_file.write("Hello World\nThis is a test\n")
        self.temp_file.close()

    async def asyncTearDown(self) -> None:
        """Clean up temporary files."""
        if os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)

    async def test_tool_properties(self) -> None:
        """Test edit tool properties."""
        self.assertEqual(self.edit_tool.name, "Edit")
        self.assertIsInstance(self.edit_tool.description, str)
        self.assertIsInstance(self.edit_tool.input_schema, dict)
        self.assertFalse(self.edit_tool.is_mcp)
        self.assertFalse(self.edit_tool.is_read_only)
        self.assertFalse(self.edit_tool.is_concurrency_safe)

    async def test_check_permissions(self) -> None:
        """Test edit tool permission checking."""
        context = PermissionContext()
        tool_input = {"file_path": "/tmp/test.txt"}
        decision = await self.edit_tool.check_permissions(tool_input, context)

        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("/tmp/test.txt", decision.message)

    async def test_simple_edit(self) -> None:
        """Test simple file editing."""
        chunks = []
        async for chunk in self.edit_tool(
            file_path=self.temp_file.name,
            old_string="Hello World",
            new_string="Hello Python",
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "running")
        self.assertTrue(chunks[0].is_last)

        # Verify file content
        with open(self.temp_file.name, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Hello Python", content)
        self.assertNotIn("Hello World", content)

    async def test_edit_not_found(self) -> None:
        """Test editing with string not found."""
        chunks = []
        async for chunk in self.edit_tool(
            file_path=self.temp_file.name,
            old_string="NonExistent",
            new_string="Something",
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "error")
        self.assertIn("not found", chunks[0].content[0].text)

    async def test_edit_multiple_occurrences(self) -> None:
        """Test editing with multiple occurrences."""
        # Write file with duplicate content
        with open(self.temp_file.name, "w", encoding="utf-8") as f:
            f.write("test\ntest\ntest\n")

        chunks = []
        async for chunk in self.edit_tool(
            file_path=self.temp_file.name,
            old_string="test",
            new_string="replaced",
        ):
            chunks.append(chunk)

        # Should fail without replace_all
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "error")

    async def test_edit_replace_all(self) -> None:
        """Test editing with replace_all flag."""
        # Write file with duplicate content
        with open(self.temp_file.name, "w", encoding="utf-8") as f:
            f.write("test\ntest\ntest\n")

        chunks = []
        async for chunk in self.edit_tool(
            file_path=self.temp_file.name,
            old_string="test",
            new_string="replaced",
            replace_all=True,
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "running")

        # Verify all occurrences replaced
        with open(self.temp_file.name, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content.count("replaced"), 3)
        self.assertEqual(content.count("test"), 0)
