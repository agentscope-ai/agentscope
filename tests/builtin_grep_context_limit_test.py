# -*- coding: utf-8 -*-
"""Regression tests for head_limit when context lines are requested.

``head_limit`` is documented as a limit on results, not on raw output
lines.  With context enabled ripgrep emits extra lines around every
match, so a limit that counts those lines can consume its whole budget
before reaching the line that actually matched.
"""

import os
import shutil
import tempfile
import unittest

from agentscope.tool import Grep


def _has_ripgrep() -> bool:
    """Whether the ``rg`` binary is available for the tool to call."""
    return shutil.which("rg") is not None


class GrepContextHeadLimitTest(unittest.IsolatedAsyncioTestCase):
    """A small head_limit must not hide the matching line."""

    async def asyncSetUp(self) -> None:
        """Create a log file whose single match sits after many lines."""
        self.temp_dir = tempfile.mkdtemp()
        lines = [f"line{index:02d}: all good" for index in range(1, 21)]
        lines[13] = "line14: ERROR database connection refused"
        with open(
            os.path.join(self.temp_dir, "server.log"),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write("\n".join(lines) + "\n")
        self.grep = Grep()

    @unittest.skipUnless(_has_ripgrep(), "ripgrep is required")
    async def test_match_survives_a_small_head_limit(self) -> None:
        """Limits below the context size must still return the match."""
        for limit in (2, 3, 4, 5, 6):
            with self.subTest(limit=limit):
                result = await self.grep.call(
                    pattern="ERROR",
                    path=self.temp_dir,
                    output_mode="content",
                    context=4,
                    head_limit=limit,
                )
                text = result.content[0].text
                self.assertIn("ERROR", text)

    def test_context_line_detection(self) -> None:
        """The match/context distinction must follow ripgrep's format."""
        # ripgrep writes ``<path>:<number>:<text>`` for a match and
        # ``<path>-<number>-<text>`` for surrounding context.
        self.assertFalse(Grep._is_context_line("path/to/log:14:ERROR boom"))
        self.assertTrue(Grep._is_context_line("path/to/log-13-line13: ok"))
        self.assertTrue(Grep._is_context_line("path/to/log-15-line15: ok"))
        # Paths that contain the delimiters themselves stay intact.
        self.assertFalse(
            Grep._is_context_line(r"C:\logs\a-b\server:14:ERROR boom"),
        )
