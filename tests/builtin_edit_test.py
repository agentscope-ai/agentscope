# -*- coding: utf-8 -*-
"""Edit tool test case."""
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.state import AgentState
from agentscope.tool import Edit, Read
from agentscope.permission import (
    PermissionContext,
    PermissionBehavior,
    PermissionEngine,
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
        old_string_schema = self.edit_tool.input_schema["properties"][
            "old_string"
        ]
        self.assertEqual(old_string_schema["minLength"], 1)
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

    async def test_empty_target_preserves_file(self) -> None:
        """An empty target must not expand between every character."""
        for original in (b"abc\r\ndef\r\n", b""):
            for replace_all in (False, True):
                with self.subTest(original=original, replace_all=replace_all):
                    with open(self.temp_file.name, "wb") as stream:
                        stream.write(original)
                    chunk = await self.edit_tool(
                        file_path=self.temp_file.name,
                        old_string="",
                        new_string="X",
                        replace_all=replace_all,
                    )
                    with open(self.temp_file.name, "rb") as stream:
                        self.assertEqual(stream.read(), original)
                    self.assertEqual(
                        chunk.model_dump(
                            exclude={
                                "id": True,
                                "content": {"__all__": {"id", "created_at"}},
                            },
                        ),
                        {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Error: old_string must not be empty. "
                                        "Use the Write tool to populate "
                                        "an empty file."
                                    ),
                                    "finished_at": None,
                                },
                            ],
                            "state": "error",
                            "is_last": True,
                            "metadata": {},
                        },
                    )

    async def test_preserves_crlf_line_endings(self) -> None:
        """A CRLF file keeps its line endings after an edit."""
        with open(self.temp_file.name, "wb") as stream:
            stream.write(b"line1\r\nHello World\r\nline3\r\n")

        chunk = await self.edit_tool(
            file_path=self.temp_file.name,
            old_string="Hello World",
            new_string="Hi",
        )

        self.assertEqual(chunk.state, "running")
        with open(self.temp_file.name, "rb") as stream:
            self.assertEqual(
                stream.read(),
                b"line1\r\nHi\r\nline3\r\n",
            )

    async def test_preserves_crlf_line_endings_after_read(self) -> None:
        """The cached Read text must not flatten CRLF on a later edit."""
        with open(self.temp_file.name, "wb") as stream:
            stream.write(b"line1\r\nHello World\r\nline3\r\n")
        state = AgentState()

        await Read()(
            file_path=self.temp_file.name,
            _agent_state=state,
        )
        chunk = await self.edit_tool(
            file_path=self.temp_file.name,
            old_string="Hello World",
            new_string="Hi",
            _agent_state=state,
        )

        self.assertEqual(chunk.state, "running")
        with open(self.temp_file.name, "rb") as stream:
            self.assertEqual(
                stream.read(),
                b"line1\r\nHi\r\nline3\r\n",
            )

    async def test_whitespace_target_can_be_deleted(self) -> None:
        """A nonempty whitespace target and an empty replacement are valid."""
        with open(self.temp_file.name, "w", encoding="utf-8") as stream:
            stream.write("a b c")
        chunk = await self.edit_tool(
            file_path=self.temp_file.name,
            old_string=" ",
            new_string="",
            replace_all=True,
        )
        self.assertEqual(chunk.state, "running")
        with open(self.temp_file.name, "r", encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "abc")

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
            await self.edit_tool.match_rule(
                "test.py",
                {"file_path": "test.py"},
            ),
        )

        # Test wildcard pattern
        self.assertTrue(
            await self.edit_tool.match_rule(
                "*.py",
                {"file_path": "test.py"},
            ),
        )

        # Test directory pattern
        self.assertTrue(
            await self.edit_tool.match_rule(
                "/tmp/**",
                {"file_path": "/tmp/test.py"},
            ),
        )

        # Test non-matching pattern
        self.assertFalse(
            await self.edit_tool.match_rule(
                "*.txt",
                {"file_path": "test.py"},
            ),
        )

        # Test empty file_path
        self.assertFalse(
            await self.edit_tool.match_rule(
                "*.py",
                {"file_path": ""},
            ),
        )

    async def test_generate_suggestions(self) -> None:
        """Test generate_suggestions for file operations."""

        # Test suggestion for file in subdirectory
        suggestions = await self.edit_tool.generate_suggestions(
            {"file_path": "/tmp/project/src/main.py"},
        )

        self.assertIsInstance(suggestions, list)
        self.assertGreater(len(suggestions), 0)
        self.assertIsInstance(suggestions[0], PermissionRule)

        # Should suggest parent directory pattern
        suggestion_contents = [s.rule_content for s in suggestions]
        self.assertIn("/tmp/project/src/**", suggestion_contents)

        # Test suggestion for file in root
        suggestions = await self.edit_tool.generate_suggestions(
            {"file_path": "/test.py"},
        )
        self.assertGreater(len(suggestions), 0)

    async def test_permission_suggestions_escape_parent(self) -> None:
        """Test approved suggestions cover only the literal parent."""
        for parent, escaped, sibling in (
            ("reports", "reports", "reports-other"),
            ("reports [2024]", "reports [[]2024]", "reports 2"),
            ("reports ?", "reports [?]", "reports x"),
            ("reports *", "reports [*]", "reports other"),
        ):
            with self.subTest(parent=parent):
                tool_input = {
                    "file_path": f"/tmp/probe/{parent}/out.csv",
                    "old_string": "a",
                    "new_string": "b",
                }
                engine = PermissionEngine(PermissionContext())
                first = await engine.check_permission(
                    self.edit_tool,
                    tool_input,
                )
                self.assertEqual(
                    first.suggested_rules,
                    [
                        PermissionRule(
                            tool_name="Edit",
                            rule_content=f"/tmp/probe/{escaped}/**",
                            behavior=PermissionBehavior.ALLOW,
                            source="suggested",
                        ),
                    ],
                )

                engine.add_rule(first.suggested_rules[0])
                approved = await engine.check_permission(
                    self.edit_tool,
                    tool_input,
                )
                unrelated = await engine.check_permission(
                    self.edit_tool,
                    {
                        **tool_input,
                        "file_path": f"/tmp/probe/{sibling}/out.csv",
                    },
                )
                self.assertEqual(
                    [
                        first.behavior,
                        approved.behavior,
                        unrelated.behavior,
                    ],
                    [
                        PermissionBehavior.ASK,
                        PermissionBehavior.ALLOW,
                        PermissionBehavior.ASK,
                    ],
                )
