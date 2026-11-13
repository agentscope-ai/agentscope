# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for unified-diff change counting."""
from unittest import TestCase

from agentscope.tui._messages import _diff_stats


class DiffStatsTest(TestCase):
    """File headers must not be counted as added/removed lines."""

    def test_plain_unified_diff(self) -> None:
        """A simple ---/+++ diff counts its hunk body."""
        diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n"
        self.assertEqual(_diff_stats(diff), (1, 1))

    def test_markers_in_body_are_counted(self) -> None:
        """A removed ``---`` and an added ``+++counter`` both count."""
        diff = "--- a\n+++ b\n@@ -1 +1 @@\n----\n+++counter;\n"
        self.assertEqual(_diff_stats(diff), (1, 1))

    def test_header_like_body_lines_are_still_counted(self) -> None:
        """Body lines that look like file headers remain real changes."""
        diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n--- item\n+++ item\n"
        self.assertEqual(_diff_stats(diff), (1, 1))

    def test_diff_without_headers(self) -> None:
        """Bare +/- lines are counted when no file header is present."""
        self.assertEqual(_diff_stats("-old\n+new\n"), (1, 1))
        self.assertEqual(_diff_stats(""), (0, 0))

    def test_preamble_headers_are_not_counted(self) -> None:
        """A git-style preamble must not inflate the counts."""
        diff = (
            "diff --git a/x b/x\n"
            "index 1234567..89abcde 100644\n"
            "--- a/x\n"
            "+++ b/x\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        self.assertEqual(_diff_stats(diff), (1, 1))
