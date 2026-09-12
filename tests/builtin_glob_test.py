# -*- coding: utf-8 -*-
"""Glob tool test case."""
import os
import tempfile
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString
from agentscope.tool import Glob
from agentscope.permission import (
    PermissionContext,
    PermissionBehavior,
    PermissionRule,
)


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
        self.assertIn("limit", self.glob_tool.input_schema["properties"])
        self.assertNotIn("limit", self.glob_tool.input_schema["required"])
        self.assertEqual(
            self.glob_tool.input_schema["properties"]["limit"]["minimum"],
            1,
        )
        self.assertEqual(
            self.glob_tool.input_schema["properties"]["limit"]["default"],
            200,
        )
        self.assertEqual(
            self.glob_tool.input_schema["properties"]["offset"]["minimum"],
            0,
        )
        self.assertEqual(
            self.glob_tool.input_schema["properties"]["offset"]["default"],
            0,
        )
        self.assertNotIn("offset", self.glob_tool.input_schema["required"])

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

    async def test_default_limit_truncates_large_result(self) -> None:
        """Test that broad searches do not return unbounded output."""
        for index in range(201):
            with open(
                os.path.join(self.temp_dir, f"match_{index}.log"),
                "w",
                encoding="utf-8",
            ):
                pass

        chunk = await self.glob_tool(
            pattern="match_*.log",
            path=self.temp_dir,
        )

        paths = [
            line
            for line in chunk.content[0].text.splitlines()
            if line.startswith(self.temp_dir)
        ]
        self.assertEqual(len(paths), 200)
        self.assertIn(
            "showing results 1-200 of 201 matches",
            chunk.content[0].text,
        )
        self.assertIn("increase limit up to 1000", chunk.content[0].text)

    async def test_explicit_limit_truncates_result(self) -> None:
        """Test that callers can request a smaller result limit."""
        for index in range(4):
            with open(
                os.path.join(self.temp_dir, f"limited_{index}.txt"),
                "w",
                encoding="utf-8",
            ):
                pass

        chunk = await self.glob_tool(
            pattern="limited_*.txt",
            path=self.temp_dir,
            limit=2,
        )

        paths = [
            line
            for line in chunk.content[0].text.splitlines()
            if line.startswith(self.temp_dir)
        ]
        self.assertEqual(len(paths), 2)
        self.assertIn(
            "showing results 1-2 of 4 matches",
            chunk.content[0].text,
        )

        complete_chunk = await self.glob_tool(
            pattern="limited_*.txt",
            path=self.temp_dir,
            limit=4,
        )
        self.assertNotIn("Results truncated", complete_chunk.content[0].text)

    async def test_invalid_limit_returns_error(self) -> None:
        """Test that invalid limit values are rejected."""
        invalid_limits: tuple[Any, ...] = (0, -1, 1.5, "1", True)
        for invalid_limit in invalid_limits:
            chunk = await self.glob_tool(
                pattern="*.py",
                path=self.temp_dir,
                limit=invalid_limit,
            )

            self.assertEqual(chunk.state, "error")
            self.assertEqual(
                chunk.content[0].text,
                "Glob limit must be a positive integer.",
            )

    async def test_limit_is_capped_at_maximum(self) -> None:
        """Test that an excessive limit cannot bypass the hard maximum."""
        for index in range(1001):
            with open(
                os.path.join(self.temp_dir, f"capped_{index}.txt"),
                "w",
                encoding="utf-8",
            ):
                pass

        chunk = await self.glob_tool(
            pattern="capped_*.txt",
            path=self.temp_dir,
            limit=1001,
        )

        paths = [
            line
            for line in chunk.content[0].text.splitlines()
            if line.startswith(self.temp_dir)
        ]
        self.assertEqual(len(paths), 1000)
        self.assertIn(
            "showing results 1-1000 of 1001 matches",
            chunk.content[0].text,
        )
        self.assertNotIn("increase limit", chunk.content[0].text)

    async def test_offset_paginates_results(self) -> None:
        """Test that callers can retrieve later pages of results."""
        for index in range(6):
            with open(
                os.path.join(self.temp_dir, f"paged_{index}.txt"),
                "w",
                encoding="utf-8",
            ):
                pass

        first_page = await self.glob_tool(
            pattern="paged_*.txt",
            path=self.temp_dir,
            limit=2,
            offset=0,
        )
        second_page = await self.glob_tool(
            pattern="paged_*.txt",
            path=self.temp_dir,
            limit=2,
            offset=2,
        )

        first_paths = {
            line
            for line in first_page.content[0].text.splitlines()
            if line.startswith(self.temp_dir)
        }
        second_paths = {
            line
            for line in second_page.content[0].text.splitlines()
            if line.startswith(self.temp_dir)
        }
        self.assertEqual(len(first_paths), 2)
        self.assertEqual(len(second_paths), 2)
        self.assertTrue(first_paths.isdisjoint(second_paths))
        self.assertIn(
            "showing results 3-4 of 6 matches",
            second_page.content[0].text,
        )
        self.assertIn("offset=4", second_page.content[0].text)

        last_page = await self.glob_tool(
            pattern="paged_*.txt",
            path=self.temp_dir,
            limit=2,
            offset=4,
        )
        last_paths = {
            line
            for line in last_page.content[0].text.splitlines()
            if line.startswith(self.temp_dir)
        }
        self.assertEqual(len(last_paths), 2)
        self.assertNotIn("Results truncated", last_page.content[0].text)

        no_more_pages = await self.glob_tool(
            pattern="paged_*.txt",
            path=self.temp_dir,
            limit=2,
            offset=6,
        )
        self.assertIn("No more files found", no_more_pages.content[0].text)

    async def test_invalid_offset_returns_error(self) -> None:
        """Test that invalid offset values are rejected."""
        invalid_offsets: tuple[Any, ...] = (-1, 1.5, "1", True)
        for invalid_offset in invalid_offsets:
            chunk = await self.glob_tool(
                pattern="*.py",
                path=self.temp_dir,
                offset=invalid_offset,
            )

            self.assertEqual(chunk.state, "error")
            self.assertEqual(
                chunk.content[0].text,
                "Glob offset must be a non-negative integer.",
            )

    async def test_windows_style_separator_pattern(self) -> None:
        """Test glob patterns that use backslashes as path separators."""
        chunk = await self.glob_tool(
            pattern=r"subdir\*.py",
            path=self.temp_dir,
        )

        self.assertDictEqual(
            chunk.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "text": os.path.join(
                            self.temp_dir,
                            "subdir",
                            "test3.py",
                        ),
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

    async def test_no_matches(self) -> None:
        """Test pattern with no matches."""
        chunk = await self.glob_tool(
            pattern="*.nonexistent",
            path=self.temp_dir,
        )

        self.assertEqual(chunk.state, "running")
        self.assertIn("No files found", chunk.content[0].text)

        no_more_pages = await self.glob_tool(
            pattern="*.nonexistent",
            path=self.temp_dir,
            offset=1,
        )
        self.assertIn("No more files found", no_more_pages.content[0].text)

    async def test_match_rule_path(self) -> None:
        """Test match_rule with path patterns."""
        # Test matching explicit path
        self.assertTrue(
            await self.glob_tool.match_rule(
                self.temp_dir,
                {"path": self.temp_dir, "pattern": "*.py"},
            ),
        )

        # Test wildcard pattern matching path
        parent_dir = os.path.dirname(self.temp_dir)
        self.assertTrue(
            await self.glob_tool.match_rule(
                parent_dir + "/**",
                {"path": self.temp_dir, "pattern": "*.py"},
            ),
        )

        # Test non-matching path
        self.assertFalse(
            await self.glob_tool.match_rule(
                "/some/other/path/**",
                {"path": self.temp_dir, "pattern": "*.py"},
            ),
        )

    async def test_match_rule_pattern(self) -> None:
        """Test match_rule with pattern matching."""
        # Test matching against the pattern itself
        self.assertTrue(
            await self.glob_tool.match_rule(
                "*.py",
                {"pattern": "*.py"},
            ),
        )

        # Test wildcard pattern matching
        self.assertTrue(
            await self.glob_tool.match_rule(
                "**/*.py",
                {"pattern": "src/**/*.py"},
            ),
        )

        # Test non-matching pattern
        self.assertFalse(
            await self.glob_tool.match_rule(
                "*.txt",
                {"pattern": "*.py"},
            ),
        )

    async def test_match_rule_path_priority(self) -> None:
        """Test that path matching takes priority over pattern matching."""
        # If path matches, should return True even if pattern doesn't
        self.assertTrue(
            await self.glob_tool.match_rule(
                self.temp_dir,
                {"path": self.temp_dir, "pattern": "*.txt"},
            ),
        )

    async def test_generate_suggestions_with_path(self) -> None:
        """Test generate_suggestions for glob with explicit path."""

        suggestions = await self.glob_tool.generate_suggestions(
            {"path": self.temp_dir, "pattern": "*.py"},
        )

        self.assertIsInstance(suggestions, list)
        self.assertGreater(len(suggestions), 0)
        self.assertIsInstance(suggestions[0], PermissionRule)

        # Should suggest directory pattern
        abs_path = os.path.abspath(self.temp_dir)
        expected_pattern = abs_path.rstrip("/") + "/**"
        suggestion_contents = [s.rule_content for s in suggestions]
        self.assertIn(expected_pattern, suggestion_contents)

    async def test_generate_suggestions_defaults_to_cwd(self) -> None:
        """Test generate_suggestions defaults to cwd when no path provided."""

        suggestions = await self.glob_tool.generate_suggestions(
            {"pattern": "*.py"},
        )

        self.assertIsInstance(suggestions, list)
        self.assertGreater(len(suggestions), 0)

        cwd = os.getcwd()
        expected_pattern = os.path.abspath(cwd).rstrip("/") + "/**"
        suggestion_contents = [s.rule_content for s in suggestions]
        self.assertIn(expected_pattern, suggestion_contents)
