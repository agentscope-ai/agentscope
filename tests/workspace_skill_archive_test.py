# -*- coding: utf-8 -*-
"""Tests for workspace skill archive validation and extraction."""

import io
import os
import tarfile
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.workspace import LocalWorkspace
from agentscope.workspace._skill import (
    build_skill_archive,
    extract_skill_archive,
    validate_skill_archive,
)


def _archive_with_member(
    name: str,
    content: bytes,
    member_type: bytes = tarfile.REGTYPE,
) -> bytes:
    """Return a tar archive containing one custom member."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.type = member_type
        info.size = len(content) if member_type == tarfile.REGTYPE else 0
        tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class WorkspaceSkillArchiveTest(TestCase):
    """Exercise the portable skill archive boundary."""

    def test_build_validate_and_extract_skill_archive(self) -> None:
        """A trusted skill directory round-trips without its root name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "source-skill"
            (skill_dir / "scripts").mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: demo\ndescription: Demo skill\n---\nBody\n",
                encoding="utf-8",
            )
            (skill_dir / "scripts" / "run.py").write_text(
                "print('ok')\n",
                encoding="utf-8",
            )

            archive = build_skill_archive(str(skill_dir))
            metadata = validate_skill_archive(archive)
            destination = Path(tmpdir) / "extracted"
            extract_skill_archive(archive, str(destination))

            self.assertEqual(metadata.name, "demo")
            self.assertEqual(metadata.description, "Demo skill")
            self.assertTrue((destination / "SKILL.md").is_file())
            self.assertEqual(
                (destination / "scripts" / "run.py").read_text(
                    encoding="utf-8",
                ),
                "print('ok')\n",
            )

    def test_rejects_unsafe_archive_members(self) -> None:
        """Traversal and link entries cannot reach outside extraction."""
        skill_md = b"---\nname: demo\ndescription: test\n---\n"
        invalid_archives = (
            _archive_with_member("../SKILL.md", skill_md),
            _archive_with_member("/SKILL.md", skill_md),
            _archive_with_member("C:/SKILL.md", skill_md),
            _archive_with_member(
                "SKILL.md",
                b"",
                member_type=tarfile.SYMTYPE,
            ),
        )

        for archive in invalid_archives:
            with self.subTest():
                with self.assertRaises(ValueError):
                    validate_skill_archive(archive)

    def test_rejects_compressed_archives(self) -> None:
        """Only deterministic uncompressed tar bytes cross the API."""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            content = b"---\nname: demo\ndescription: test\n---\n"
            info = tarfile.TarInfo("SKILL.md")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

        with self.assertRaisesRegex(ValueError, "valid tar archive"):
            validate_skill_archive(buffer.getvalue())

    def test_rejects_non_string_frontmatter_fields(self) -> None:
        """Skill metadata must use non-empty string values."""
        archive = _archive_with_member(
            "SKILL.md",
            b"---\nname:\n  - demo\ndescription: test\n---\n",
        )

        with self.assertRaisesRegex(ValueError, "string name"):
            validate_skill_archive(archive)

    def test_build_rejects_symlinks(self) -> None:
        """Trusted local packaging does not dereference nested links."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: demo\ndescription: test\n---\n",
                encoding="utf-8",
            )
            try:
                os.symlink(skill_dir / "SKILL.md", skill_dir / "linked.md")
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"Symlinks are unavailable: {error}")

            with self.assertRaisesRegex(ValueError, "regular files only"):
                build_skill_archive(str(skill_dir))


class LocalWorkspaceSkillUploadTest(IsolatedAsyncioTestCase):
    """Exercise the archive API through the local workspace."""

    async def test_add_skill_from_archive(self) -> None:
        """Runtime uploads are extracted and indexed as normal skills."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "source"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: uploaded\ndescription: Uploaded skill\n---\n",
                encoding="utf-8",
            )
            (skill_dir / "tool.py").write_text(
                "VALUE = 1\n",
                encoding="utf-8",
            )
            workspace = LocalWorkspace(workdir=str(Path(tmpdir) / "work"))
            await workspace.initialize()
            try:
                await workspace.add_skill(
                    build_skill_archive(str(skill_dir)),
                )

                skills = await workspace.list_skills()
                self.assertEqual(
                    [skill.name for skill in skills],
                    ["uploaded"],
                )
                self.assertTrue(
                    (Path(skills[0].dir) / "tool.py").is_file(),
                )
            finally:
                await workspace.close()
