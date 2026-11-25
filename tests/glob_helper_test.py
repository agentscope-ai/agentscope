# -*- coding: utf-8 -*-
"""Regression tests for glob matching of newline-containing filenames."""
import fnmatch
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

# The helper is deployed without agentscope installed in remote workspaces.
_HELPER_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/agentscope/tool/_builtin/_scripts/_glob_helper.py"
)
_glob_part_to_regex = runpy.run_path(str(_HELPER_PATH))["_glob_part_to_regex"]


class GlobHelperTest(unittest.TestCase):
    """Check wildcard and exact matching independently of host filenames."""

    def test_star_matches_newline(self) -> None:
        """A star matches all characters within a filename segment."""
        self.assertIsNotNone(
            _glob_part_to_regex("*.py").match("report\npart.py"),
        )

    def test_question_mark_matches_newline(self) -> None:
        """A question mark also matches a single newline character."""
        self.assertIsNotNone(
            _glob_part_to_regex("report?.py").match("report\n.py"),
        )

    def test_literal_rejects_trailing_newline(self) -> None:
        """An exact filename must not match an extra trailing newline."""
        self.assertIsNone(
            _glob_part_to_regex("report.py").match("report.py\n"),
        )

    def test_extension_rejects_trailing_newline(self) -> None:
        """A suffix must reach the actual end of the filename."""
        self.assertIsNone(
            _glob_part_to_regex("*.py").match("report.py\n"),
        )

    def test_ordinary_filename_controls(self) -> None:
        """Preserve wildcard, literal metacharacter, and suffix behavior."""
        for pattern, name, expected in (
            ("*.py", "report.py", True),
            ("*.py", "report.txt", False),
            ("report?.py", "report1.py", True),
            ("report?.py", "report12.py", False),
            ("report.py", "reportXpy", False),
            ("[report].py", "[report].py", True),
            ("[report].py", "r.py", False),
            ("*.py", "", False),
            ("*", "report\n", True),
        ):
            with self.subTest(pattern=pattern, name=name):
                self.assertEqual(
                    _glob_part_to_regex(pattern).match(name) is not None,
                    expected,
                )

    @unittest.skipIf(os.name == "nt", "Windows forbids newlines in filenames")
    def test_helper_cli_with_real_newline_filenames(self) -> None:
        """Match real POSIX filenames through the deployed helper CLI."""
        names = ["report.py", "report\npart.py", "report\n.py", "report.py\n"]
        with tempfile.TemporaryDirectory() as directory:
            for name in names:
                with open(
                    os.path.join(directory, name),
                    "w",
                    encoding="utf-8",
                ):
                    pass
            for pattern in ("*.py", "report?.py", "report.py", "*"):
                with self.subTest(pattern=pattern):
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(_HELPER_PATH),
                            f"--pattern={pattern}",
                            f"--base-dir={directory}",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    self.assertCountEqual(
                        json.loads(result.stdout),
                        [
                            os.path.join(directory, name)
                            for name in names
                            if fnmatch.fnmatchcase(name, pattern)
                        ],
                    )
