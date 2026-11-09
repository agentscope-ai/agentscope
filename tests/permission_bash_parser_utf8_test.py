# -*- coding: utf-8 -*-
"""Regression tests for path extraction on non-ASCII commands.

tree-sitter reports byte offsets into the UTF-8 encoding of the source.
Slicing the ``str`` with those offsets only agrees while the command is
ASCII, so these tests pin the behaviour for commands that are not.
"""

import unittest

from agentscope.tool._builtin._bash_parser import BashCommandParser


class NonAsciiPathExtractionTest(unittest.TestCase):
    """A non-ASCII argument must not hide the arguments after it."""

    def setUp(self) -> None:
        """Create a parser for each test."""
        self.parser = BashCommandParser()

    def test_argument_after_non_ascii_one_is_still_extracted(self) -> None:
        """`.env` must remain a target when an earlier argument is CJK."""
        paths = self.parser.extract_file_paths("rm 数据.txt .env")

        self.assertEqual(
            paths,
            [("rm", "数据.txt"), ("rm", ".env")],
        )

    def test_ascii_command_is_unchanged(self) -> None:
        """The ASCII path keeps working exactly as before."""
        paths = self.parser.extract_file_paths("rm data.txt .env")

        self.assertEqual(
            paths,
            [("rm", "data.txt"), ("rm", ".env")],
        )

    def test_redirect_after_non_ascii_argument_is_not_truncated(self) -> None:
        """The redirect target must not lose its leading characters."""
        paths = self.parser.extract_file_paths(
            "touch 数据 >> ../outside.txt",
        )

        self.assertEqual(
            paths,
            [("touch", "数据"), ("redirect", "../outside.txt")],
        )

    def test_extracted_paths_resolve_to_the_arguments_sent(self) -> None:
        """Every extracted path must be one of the real command words."""
        command = "mv 报告.md 备份.md → 笔记.md"
        # The arrow is not a shell operator, so it stays an argument; the
        # point of the assertion is that no path is shifted relative to
        # the text tree-sitter parsed.
        paths = self.parser.extract_file_paths(command)
        targets = [path for _cmd, path in paths]

        self.assertIn("报告.md", targets)
        self.assertIn("备份.md", targets)
        for target in targets:
            self.assertIn(target, command)
