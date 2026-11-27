# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for the shared workspace skill installers."""
import io
import os
import sys
import tarfile
import tempfile
from collections.abc import AsyncIterator
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import LocalBackend
from agentscope.workspace import WorkspaceBase


class _BareWorkspace(WorkspaceBase):  # pylint: disable=abstract-method
    """A workspace using the shared skill installers over a local backend.

    ``LocalWorkspace`` overrides both installers with its indexed variant;
    this subclass keeps the base implementations reachable without a sandbox.
    """

    def __init__(self, workdir: str) -> None:
        """Bind a local backend over ``workdir``."""
        super().__init__()
        self.workdir = workdir
        self._backend = LocalBackend()

    async def initialize(self) -> None:
        """Nothing to provision for this workspace."""

    async def close(self) -> None:
        """Nothing to release for this workspace."""

    @property
    def _python_command(self) -> str:
        """Run the installer shims with this interpreter."""
        return sys.executable

    async def _equip_partition(self, agent_id: str | None) -> str:
        """Return the partition without copying the seed template."""
        partition = self._skill_partition(agent_id)
        os.makedirs(partition, exist_ok=True)
        return partition


class TestWorkspaceBaseSkillInstall(IsolatedAsyncioTestCase):
    """Front-matter validation in the shared (sandboxed) installers."""

    @staticmethod
    def _write_skill(root: str, body: str) -> str:
        """Write a skill directory with ``body`` as its SKILL.md."""
        path = os.path.join(root, "demo")
        os.makedirs(path)
        with open(os.path.join(path, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(body)
        return path

    async def test_add_skill_rejects_missing_frontmatter(self) -> None:
        """A skill without name/description is refused, not silently lost."""
        with tempfile.TemporaryDirectory() as workdir:
            with tempfile.TemporaryDirectory() as src:
                skill_dir = self._write_skill(
                    src,
                    "---\nname: demo\n---\n\nNo description.\n",
                )
                workspace = _BareWorkspace(workdir)

                with self.assertRaisesRegex(
                    ValueError,
                    "missing or malformed",
                ):
                    await workspace.add_skill(skill_dir)

                self.assertEqual(await workspace.list_skills(), [])

    async def test_add_skill_accepts_valid_frontmatter(self) -> None:
        """The validation does not reject a complete skill."""
        with tempfile.TemporaryDirectory() as workdir:
            with tempfile.TemporaryDirectory() as src:
                skill_dir = self._write_skill(
                    src,
                    "---\nname: demo\ndescription: d\n---\n\nBody.\n",
                )
                workspace = _BareWorkspace(workdir)

                await workspace.add_skill(skill_dir)

                self.assertEqual(
                    [skill.name for skill in await workspace.list_skills()],
                    ["demo"],
                )

    async def test_add_skill_archive_rejects_missing_frontmatter(
        self,
    ) -> None:
        """An archive without usable front matter is not installed."""
        with tempfile.TemporaryDirectory() as workdir:
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as archive:
                data = b"---\nname: demo\n---\n\nNo description.\n"
                info = tarfile.TarInfo("demo/SKILL.md")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

            async def stream() -> AsyncIterator[bytes]:
                """Yield the archive in one chunk."""
                yield buffer.getvalue()

            workspace = _BareWorkspace(workdir)
            with self.assertRaisesRegex(ValueError, "missing or malformed"):
                await workspace.add_skill_archive(stream(), "tar", "demo")

            self.assertEqual(await workspace.list_skills(), [])


class TestWorkspaceBaseRemoveSkill(IsolatedAsyncioTestCase):
    """``remove_skill`` finds skills outside the default partition."""

    @staticmethod
    def _write_skill(root: str) -> str:
        """Write ``<root>/demo`` with complete front matter."""
        path = os.path.join(root, "demo")
        os.makedirs(path)
        with open(os.path.join(path, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: demo\ndescription: d\n---\n\nBody.\n")
        return path

    async def test_remove_skill_searches_every_partition(self) -> None:
        """A skill owned by an agent partition is removed by name alone.

        ``remove_skill(agent_id=None)`` documents that it searches every
        partition, but it only listed the default one, so a skill owned
        by an agent was reported as missing.
        """
        with tempfile.TemporaryDirectory() as workdir:
            with tempfile.TemporaryDirectory() as src:
                workspace = _BareWorkspace(workdir)
                await workspace.add_skill(
                    self._write_skill(src),
                    agent_id="agent-a",
                )
                self.assertEqual(
                    len(await workspace.list_skills(agent_id="agent-a")),
                    1,
                )

                await workspace.remove_skill("demo")

                self.assertEqual(
                    await workspace.list_skills(agent_id="agent-a"),
                    [],
                )

    async def test_remove_skill_still_reports_unknown_names(self) -> None:
        """An unknown name raises, as documented, after the sweep."""
        with tempfile.TemporaryDirectory() as workdir:
            workspace = _BareWorkspace(workdir)
            await workspace.list_skills(agent_id="agent-a")
            with self.assertRaisesRegex(KeyError, "not found"):
                await workspace.remove_skill("demo")
