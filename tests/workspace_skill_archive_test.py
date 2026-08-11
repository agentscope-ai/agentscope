# -*- coding: utf-8 -*-
"""Test cases for installing a skill from an archive stream."""
import io
import os
import tarfile
import tempfile
import zipfile
from typing import AsyncIterator
from unittest.async_case import IsolatedAsyncioTestCase

from fastapi import UploadFile

from agentscope.app._service import WorkspaceService
from agentscope.app._service._workspace import (
    SkillUploadError,
    UploadManifest,
)
from agentscope.workspace import LocalWorkspace
from agentscope.workspace._base import WorkspaceBase


async def _chunks(data: bytes, size: int = 11) -> AsyncIterator[bytes]:
    """Yield ``data`` in small pieces, so streaming is actually tested."""
    for i in range(0, len(data), size):
        yield data[i : i + size]


def _zip(files: dict[str, str]) -> bytes:
    """Build a ZIP archive from a path -> content mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buf.getvalue()


def _tar(files: dict[str, str]) -> bytes:
    """Build a tar archive from a path -> content mapping."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        for path, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _skill_md(name: str) -> str:
    """Return a minimal valid ``SKILL.md``."""
    return f"---\nname: {name}\ndescription: a test skill\n---\nbody"


class AddSkillArchiveLocalTest(IsolatedAsyncioTestCase):
    """The LocalWorkspace override, which reuses the path installer."""

    async def asyncSetUp(self) -> None:
        """Open a workspace in a temporary directory."""
        # enterContext is the unittest equivalent of "with", which
        # pylint does not recognize.
        # pylint: disable=consider-using-with
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.workspace = LocalWorkspace(workdir=self.tmp)
        await self.workspace.initialize()

    async def test_install_zip_and_tar(self) -> None:
        """Both formats land, named from the front matter."""
        await self.workspace.add_skill_archive(
            _chunks(_zip({"pack/SKILL.md": _skill_md("alpha")})),
            "zip",
            "pack",
        )
        await self.workspace.add_skill_archive(
            _chunks(_tar({"pack/SKILL.md": _skill_md("beta")})),
            "tar",
            "pack",
        )
        names = sorted(s.name for s in await self.workspace.list_skills())
        self.assertEqual(names, ["alpha", "beta"])

    async def test_duplicate_is_skipped(self) -> None:
        """The same skill twice is deduped by SKILL.md hash."""
        payload = _zip({"pack/SKILL.md": _skill_md("alpha")})
        await self.workspace.add_skill_archive(
            _chunks(payload),
            "zip",
            "pack",
        )
        await self.workspace.add_skill_archive(
            _chunks(payload),
            "zip",
            "pack",
        )
        self.assertEqual(len(await self.workspace.list_skills()), 1)

    async def test_traversing_archive_is_refused(self) -> None:
        """A member escaping the staging directory fails the install."""
        with self.assertRaises(RuntimeError):
            await self.workspace.add_skill_archive(
                _chunks(_zip({"../evil.txt": "pwn"})),
                "zip",
                "pack",
            )
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp, "..", "evil.txt")),
        )

    async def test_archive_without_skill_md_is_refused(self) -> None:
        """An archive with no SKILL.md anywhere is rejected."""
        with self.assertRaises(ValueError):
            await self.workspace.add_skill_archive(
                _chunks(_zip({"pack/readme.md": "hi"})),
                "zip",
                "pack",
            )

    async def test_nothing_is_left_behind(self) -> None:
        """Staging directories and archives do not survive an install."""
        await self.workspace.add_skill_archive(
            _chunks(_zip({"pack/SKILL.md": _skill_md("alpha")})),
            "zip",
            "pack",
        )
        # No ``.mcp``: it is only written once an agent/session
        # diverges from ``default_mcps``.
        self.assertEqual(sorted(os.listdir(self.tmp)), ["skills"])


class AddSkillArchiveSandboxedTest(IsolatedAsyncioTestCase):
    """The base implementation, exercised over a local backend.

    Calling the unbound base method covers the code every sandboxed
    workspace runs, without needing a container.
    """

    async def asyncSetUp(self) -> None:
        """Open a workspace in a temporary directory."""
        # enterContext is the unittest equivalent of "with", which
        # pylint does not recognize.
        # pylint: disable=consider-using-with
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.workspace = LocalWorkspace(workdir=self.tmp)
        await self.workspace.initialize()
        self.skills_dir = os.path.join(self.tmp, "skills", "default")

    async def test_directory_name_is_suffixed_when_taken(self) -> None:
        """A repeated name gets a numeric suffix rather than an error."""
        for index in range(3):
            await WorkspaceBase.add_skill_archive(
                self.workspace,
                _chunks(_zip({"pack/SKILL.md": _skill_md(f"s{index}")})),
                "zip",
                "pack",
            )
        self.assertEqual(
            sorted(os.listdir(self.skills_dir)),
            ["pack", "pack-1", "pack-2"],
        )

    async def test_flat_archive_is_accepted(self) -> None:
        """SKILL.md at the archive root needs no wrapping folder."""
        await WorkspaceBase.add_skill_archive(
            self.workspace,
            _chunks(_zip({"SKILL.md": _skill_md("flat")})),
            "zip",
            "flat-pack",
        )
        self.assertEqual(os.listdir(self.skills_dir), ["flat-pack"])

    async def test_oversized_expansion_is_refused(self) -> None:
        """An archive expanding past the cap fails inside the sandbox."""
        payload = _zip({"pack/SKILL.md": _skill_md("big") + "A" * 5000})
        with self.assertRaises(RuntimeError) as ctx:
            await WorkspaceBase.add_skill_archive(
                self.workspace,
                _chunks(payload),
                "zip",
                "pack",
                max_extracted_bytes=100,
            )
        self.assertIn("expands to", str(ctx.exception))
        self.assertEqual(os.listdir(self.skills_dir), [])

    async def test_rejects_a_path_as_directory_name(self) -> None:
        """A dir_name with separators never reaches the filesystem."""
        with self.assertRaises(ValueError):
            await WorkspaceBase.add_skill_archive(
                self.workspace,
                _chunks(_zip({"pack/SKILL.md": _skill_md("x")})),
                "zip",
                "../escape",
            )


class UploadManifestTest(IsolatedAsyncioTestCase):
    """Manifest validation and the tar stream built from it."""

    @staticmethod
    def _manifest(files: dict[str, bytes]) -> UploadManifest:
        """Describe ``files`` the way the browser would."""
        return UploadManifest.model_validate(
            {
                "entries": [
                    {"path": path, "size": len(data)}
                    for path, data in files.items()
                ],
            },
        )

    @staticmethod
    def _uploads(files: dict[str, bytes]) -> list[UploadFile]:
        """Wrap ``files`` as multipart parts."""
        return [
            UploadFile(file=io.BytesIO(data), filename=path)
            for path, data in files.items()
        ]

    def test_root_is_returned(self) -> None:
        """A well-formed manifest yields its single root folder."""
        manifest = self._manifest(
            {"pack/SKILL.md": b"x", "pack/lib/run.py": b"y"},
        )
        self.assertEqual(
            WorkspaceService.validate_manifest(manifest, 2),
            "pack",
        )

    def test_rejects_multiple_roots(self) -> None:
        """Files from two folders are not one skill."""
        manifest = self._manifest({"a/SKILL.md": b"x", "b/run.py": b"y"})
        with self.assertRaises(SkillUploadError):
            WorkspaceService.validate_manifest(manifest, 2)

    def test_rejects_traversal(self) -> None:
        """A ``..`` segment is refused before anything is read."""
        manifest = self._manifest({"pack/../../SKILL.md": b"x"})
        with self.assertRaises(SkillUploadError):
            WorkspaceService.validate_manifest(manifest, 1)

    def test_rejects_missing_skill_md(self) -> None:
        """The root must hold a SKILL.md."""
        manifest = self._manifest({"pack/readme.md": b"x"})
        with self.assertRaises(SkillUploadError):
            WorkspaceService.validate_manifest(manifest, 1)

    def test_rejects_oversized_file(self) -> None:
        """A single part over the per-file limit is refused."""
        manifest = UploadManifest.model_validate(
            {
                "entries": [
                    {"path": "pack/SKILL.md", "size": 1},
                    {"path": "pack/big.bin", "size": 10**12},
                ],
            },
        )
        with self.assertRaises(SkillUploadError):
            WorkspaceService.validate_manifest(manifest, 2)

    def test_rejects_part_count_mismatch(self) -> None:
        """A manifest that does not describe the parts sent is refused.

        The tar headers come from the manifest, so a mismatch would
        pair one file's size with another's bytes.
        """
        manifest = self._manifest(
            {"pack/SKILL.md": b"x", "pack/lib/run.py": b"y"},
        )
        with self.assertRaises(SkillUploadError):
            WorkspaceService.validate_manifest(manifest, 1)

    async def test_stream_round_trips(self) -> None:
        """The emitted tar reads back with the same members."""
        files = {
            "pack/SKILL.md": _skill_md("gamma").encode("utf-8"),
            "pack/lib/run.py": b"print('hi')",
        }
        manifest = self._manifest(files)
        chunks = [
            chunk
            async for chunk in WorkspaceService.tar_stream(
                manifest,
                self._uploads(files),
            )
        ]

        archive = tarfile.open(fileobj=io.BytesIO(b"".join(chunks)))
        self.assertEqual(
            sorted(m.name for m in archive.getmembers()),
            ["pack/SKILL.md", "pack/lib/run.py"],
        )
        member = archive.extractfile("pack/lib/run.py")
        assert member is not None
        self.assertEqual(member.read(), b"print('hi')")

    async def test_declared_size_is_verified(self) -> None:
        """A part longer than declared aborts the stream."""
        manifest = UploadManifest.model_validate(
            {"entries": [{"path": "pack/SKILL.md", "size": 2}]},
        )
        uploads = self._uploads({"pack/SKILL.md": b"much longer"})
        with self.assertRaises(SkillUploadError):
            async for _ in WorkspaceService.tar_stream(manifest, uploads):
                pass
