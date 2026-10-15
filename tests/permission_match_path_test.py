# -*- coding: utf-8 -*-
"""A permission rule must be matched against the path the filesystem resolves.

``match_rule`` compares the raw ``file_path`` string with :func:`fnmatch.fnmatch`,
which performs no path resolution. A rule covering a directory therefore also
matched paths that merely *start* with it textually and leave it through ``..``,
so an allow rule scoped to a directory silently admitted writes outside it.
"""
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import Edit, Read, Write


class PermissionMatchPathTest(IsolatedAsyncioTestCase):
    """``match_rule`` must agree with what the filesystem will act on."""

    async def asyncSetUp(self) -> None:
        """Create the approved directory layout."""
        self.temp_dir = tempfile.mkdtemp()
        self.allowed_dir = os.path.join(self.temp_dir, "src")
        os.makedirs(os.path.join(self.allowed_dir, "sub"), exist_ok=True)
        os.makedirs(os.path.join(self.temp_dir, "outside"), exist_ok=True)
        self.rule = os.path.join(self.allowed_dir, "**")

    async def asyncTearDown(self) -> None:
        """Remove the temporary tree."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _traversal_path(self) -> str:
        """A path that is textually inside the rule but resolves outside it."""
        return os.path.join(
            self.allowed_dir,
            "..",
            "outside",
            "app.conf",
        )

    def _in_tree_path(self) -> str:
        """A path that stays inside the approved directory."""
        return os.path.join(self.allowed_dir, "main.py")

    def _in_tree_path_using_dotdot(self) -> str:
        """A path that uses ``..`` but still resolves inside the directory."""
        return os.path.join(self.allowed_dir, "sub", "..", "main.py")

    async def test_traversal_path_is_not_matched(self) -> None:
        """The escape path must not inherit the directory's allow rule."""
        for tool in (Write(), Edit(), Read()):
            with self.subTest(tool=tool.name):
                self.assertFalse(
                    await tool.match_rule(
                        self.rule,
                        {"file_path": self._traversal_path()},
                    ),
                    f"{tool.name} matched a path that resolves outside the "
                    f"allowed directory",
                )

    async def test_paths_inside_the_directory_are_still_matched(self) -> None:
        """The fix must not narrow the rule for legitimate paths."""
        for tool in (Write(), Edit(), Read()):
            with self.subTest(tool=tool.name):
                self.assertTrue(
                    await tool.match_rule(
                        self.rule,
                        {"file_path": self._in_tree_path()},
                    ),
                )

    async def test_dotdot_that_stays_inside_is_still_matched(self) -> None:
        """Normalizing must not reject paths that resolve back inside."""
        for tool in (Write(), Edit(), Read()):
            with self.subTest(tool=tool.name):
                self.assertTrue(
                    await tool.match_rule(
                        self.rule,
                        {"file_path": self._in_tree_path_using_dotdot()},
                    ),
                )

    async def test_the_three_tools_decide_identically(self) -> None:
        """Write, Edit and Read share one decision, so they must agree."""
        decisions = []
        for tool in (Write(), Edit(), Read()):
            decisions.append(
                (
                    await tool.match_rule(
                        self.rule,
                        {"file_path": self._traversal_path()},
                    ),
                    await tool.match_rule(
                        self.rule,
                        {"file_path": self._in_tree_path()},
                    ),
                ),
            )
        self.assertEqual(decisions[0], decisions[1])
        self.assertEqual(decisions[1], decisions[2])
