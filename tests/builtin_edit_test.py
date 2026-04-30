# -*- coding: utf-8 -*-
"""Edit tool test case."""
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import Edit
from agentscope.permission import (
    PermissionContext,
    PermissionBehavior,
    PermissionRule,
)


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
        """Test edit tool permission checking.

        Edit tool should return PASSTHROUGH for non-dangerous paths,
        allowing PermissionEngine to check allow rules.
        """
        context = PermissionContext()
        tool_input = {"file_path": "/tmp/test.txt"}
        decision = await self.edit_tool.check_permissions(tool_input, context)

        self.assertEqual(decision.behavior, PermissionBehavior.PASSTHROUGH)

    async def test_simple_edit(self) -> None:
        """Test simple file editing."""
        chunk = await self.edit_tool(
            file_path=self.temp_file.name,
            old_string="Hello World",
            new_string="Hello Python",
        )

        self.assertEqual(chunk.state, "running")
        self.assertTrue(chunk.is_last)

        # Verify file content
        with open(self.temp_file.name, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Hello Python", content)
        self.assertNotIn("Hello World", content)

    async def test_edit_not_found(self) -> None:
        """Test editing with string not found."""
        chunk = await self.edit_tool(
            file_path=self.temp_file.name,
            old_string="NonExistent",
            new_string="Something",
        )

        self.assertEqual(chunk.state, "error")
        self.assertIn("not found", chunk.content[0].text)

    async def test_edit_multiple_occurrences(self) -> None:
        """Test editing with multiple occurrences."""
        # Write file with duplicate content
        with open(self.temp_file.name, "w", encoding="utf-8") as f:
            f.write("test\ntest\ntest\n")

        chunk = await self.edit_tool(
            file_path=self.temp_file.name,
            old_string="test",
            new_string="replaced",
        )

        # Should fail without replace_all
        self.assertEqual(chunk.state, "error")

    async def test_edit_replace_all(self) -> None:
        """Test editing with replace_all flag."""
        # Write file with duplicate content
        with open(self.temp_file.name, "w", encoding="utf-8") as f:
            f.write("test\ntest\ntest\n")

        chunk = await self.edit_tool(
            file_path=self.temp_file.name,
            old_string="test",
            new_string="replaced",
            replace_all=True,
        )

        self.assertEqual(chunk.state, "running")

        # Verify all occurrences replaced
        with open(self.temp_file.name, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content.count("replaced"), 3)
        self.assertEqual(content.count("test"), 0)

    async def test_match_rule_glob_pattern(self) -> None:
        """Test match_rule with glob patterns."""
        # Test exact match
        self.assertTrue(
            self.edit_tool.match_rule(
                "test.py",
                {"file_path": "test.py"},
            ),
        )

        # Test wildcard pattern
        self.assertTrue(
            self.edit_tool.match_rule(
                "*.py",
                {"file_path": "test.py"},
            ),
        )

        # Test directory pattern
        self.assertTrue(
            self.edit_tool.match_rule(
                "/tmp/**",
                {"file_path": "/tmp/test.py"},
            ),
        )

        # Test non-matching pattern
        self.assertFalse(
            self.edit_tool.match_rule(
                "*.txt",
                {"file_path": "test.py"},
            ),
        )

        # Test empty file_path
        self.assertFalse(
            self.edit_tool.match_rule(
                "*.py",
                {"file_path": ""},
            ),
        )

    async def test_generate_suggestions(self) -> None:
        """Test generate_suggestions for file operations."""

        # Test suggestion for file in subdirectory
        suggestions = self.edit_tool.generate_suggestions(
            {"file_path": "/tmp/project/src/main.py"},
        )

        self.assertIsInstance(suggestions, list)
        self.assertGreater(len(suggestions), 0)
        self.assertIsInstance(suggestions[0], PermissionRule)

        # Should suggest parent directory pattern
        suggestion_contents = [s.rule_content for s in suggestions]
        self.assertIn("/tmp/project/src/**", suggestion_contents)

        # Test suggestion for file in root
        suggestions = self.edit_tool.generate_suggestions(
            {"file_path": "/test.py"},
        )
        self.assertGreater(len(suggestions), 0)
