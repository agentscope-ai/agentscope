# -*- coding: utf-8 -*-
"""Workspace searches must use the workspace rather than the launcher cwd."""
import os
import shutil
import tempfile
from pathlib import Path
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import Glob, Grep
from agentscope.workspace import LocalWorkspace


class WorkspaceSearchCwdTest(IsolatedAsyncioTestCase):
    """Exercise real search tools over two independent local workspaces."""

    async def asyncSetUp(self) -> None:
        """Keep the launcher's files distinct from both workspace trees."""
        temporary = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temporary)
        root = Path(temporary).resolve()
        self.launcher = root / "launcher"
        self.launcher.mkdir()
        (self.launcher / "launcher.py").write_text(
            "needle\n",
            encoding="utf-8",
        )
        original_cwd = os.getcwd()
        self.addCleanup(os.chdir, original_cwd)
        os.chdir(self.launcher)
        self.projects = []
        for name in ("project_a", "project_b"):
            project = root / name
            (project / "src").mkdir(parents=True)
            (project / "app.py").write_text("needle\n", encoding="utf-8")
            (project / "src" / "nested.py").write_text(
                "needle\n",
                encoding="utf-8",
            )
            workspace = LocalWorkspace(workdir=str(project))
            await workspace.initialize()
            self.addAsyncCleanup(workspace.close)
            tools = [
                tool
                for tool in await workspace.list_tools()
                if isinstance(tool, (Glob, Grep))
            ]
            self.projects.append((project, tools))

    @staticmethod
    def _input(tool: Glob | Grep, path: str | None = None) -> dict:
        """Search Python files recursively with either tool."""
        args = (
            {"pattern": "**/*.py"}
            if isinstance(tool, Glob)
            else {"pattern": "needle", "glob": "*.py"}
        )
        if path is not None:
            args["path"] = path
        return args

    async def test_default_searches_stay_in_each_workspace(self) -> None:
        """Both workspaces retain their own default without global chdir."""
        for project, tools in self.projects:
            for tool in tools:
                with self.subTest(project=project.name, tool=tool.name):
                    result = await tool(**self._input(tool))
                    self.assertEqual(
                        set(result.content[0].text.splitlines()),
                        {
                            str(project / "app.py"),
                            str(project / "src/nested.py"),
                        },
                    )
                    self.assertEqual(os.getcwd(), str(self.launcher))

    async def test_relative_search_paths_use_workspace(self) -> None:
        """Dot and nested paths resolve relative to the workspace."""
        project, tools = self.projects[0]
        for tool in tools:
            for path in (".", "src"):
                with self.subTest(tool=tool.name, path=path):
                    result = await tool(**self._input(tool, path))
                    expected = {str(project / "src/nested.py")}
                    if path == ".":
                        expected.add(str(project / "app.py"))
                    self.assertEqual(
                        set(result.content[0].text.splitlines()),
                        expected,
                    )

    async def test_absolute_path_overrides_workspace(self) -> None:
        """An explicit external path keeps its existing meaning."""
        for tool in self.projects[0][1]:
            with self.subTest(tool=tool.name):
                result = await tool(**self._input(tool, str(self.launcher)))
                self.assertEqual(
                    result.content[0].text,
                    str(self.launcher / "launcher.py"),
                )

    async def test_standalone_tools_keep_process_directory(self) -> None:
        """Unconfigured tools preserve both default and relative paths."""
        for tool in (Glob(), Grep()):
            for path in (None, "."):
                with self.subTest(tool=tool.name, path=path):
                    result = await tool(**self._input(tool, path))
                    actual = result.content[0].text
                    self.assertIn("launcher.py", actual)
                    self.assertNotIn("app.py", actual)

    async def test_permission_paths_follow_search_directory(self) -> None:
        """Rules and suggestions use the directory that will be searched."""
        project, tools = self.projects[0]
        for tool in tools:
            for path in (None, ".", "src", str(self.launcher)):
                with self.subTest(tool=tool.name, path=path):
                    args = self._input(tool, path)
                    expected = str((project / (path or ".")).resolve())
                    self.assertTrue(await tool.match_rule(expected, args))
                    if path != str(self.launcher):
                        self.assertFalse(
                            await tool.match_rule(str(self.launcher), args),
                        )
                    suggestions = await tool.generate_suggestions(args)
                    self.assertEqual(
                        suggestions[0].rule_content,
                        expected.rstrip("/\\") + "/**",
                    )

    async def test_existing_relative_and_glob_pattern_rules_still_match(
        self,
    ) -> None:
        """Configuring cwd preserves existing caller-supplied rule forms."""
        for tool in self.projects[0][1]:
            with self.subTest(tool=tool.name):
                self.assertTrue(
                    await tool.match_rule("src", self._input(tool, "src")),
                )
                self.assertTrue(await tool.match_rule(None, self._input(tool)))
                if isinstance(tool, Glob):
                    self.assertTrue(
                        await tool.match_rule("*.py", self._input(tool)),
                    )
