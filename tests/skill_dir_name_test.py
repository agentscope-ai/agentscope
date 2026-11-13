# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for skill directory-name sanitization."""
from unittest import TestCase

from agentscope.workspace._local_workspace import _sanitize_dir_name


class SanitizeDirNameTest(TestCase):
    """Skill names must map to usable directory names on every OS."""

    def test_ordinary_names_are_unchanged(self) -> None:
        """Allowed characters survive normalization unchanged."""
        self.assertEqual(_sanitize_dir_name("MySkill"), "MySkill")
        self.assertEqual(_sanitize_dir_name("my-skill_1"), "my-skill_1")
        self.assertEqual(_sanitize_dir_name("中文 Skill"), "中文_Skill")

    def test_illegal_chars_become_underscores(self) -> None:
        """Dots, slashes and spaces are replaced with underscores."""
        self.assertEqual(_sanitize_dir_name("a.b"), "a_b")
        self.assertEqual(_sanitize_dir_name("a/b"), "a_b")
        self.assertEqual(_sanitize_dir_name("!!!"), "___")

    def test_windows_reserved_names_are_escaped(self) -> None:
        """CON / NUL / COM1 etc. cannot be created on Windows."""
        for name in (
            "CON",
            "con",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "com9",
            "LPT1",
            "lpt9",
        ):
            with self.subTest(name=name):
                self.assertEqual(_sanitize_dir_name(name), name + "_")

    def test_empty_name_is_not_returned_empty(self) -> None:
        """An empty skill name must not produce an empty directory name."""
        self.assertEqual(_sanitize_dir_name(""), "_")
