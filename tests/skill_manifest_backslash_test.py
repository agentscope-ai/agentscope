# -*- coding: utf-8 -*-
"""Regression tests for Windows-style paths in skill upload manifests."""
from unittest import TestCase

from agentscope.app._service._workspace import (
    SkillUploadError,
    UploadEntry,
    UploadManifest,
    WorkspaceService,
)


def _manifest(paths: list[str]) -> UploadManifest:
    """Build a manifest whose entries all declare a small size."""
    return UploadManifest(
        entries=[UploadEntry(path=p, size=10) for p in paths],
    )


class SkillManifestBackslashTest(TestCase):
    """Path checks and the SKILL.md check must use the same normalisation."""

    def test_forward_slash_manifest_is_accepted(self) -> None:
        """The documented layout is unchanged (control)."""
        root = WorkspaceService.validate_manifest(
            _manifest(["myskill/SKILL.md", "myskill/README.md"]),
            2,
        )
        self.assertEqual(root, "myskill")

    def test_backslash_manifest_is_accepted(self) -> None:
        """Windows separators must not fail the SKILL.md lookup.

        ``validate_manifest`` normalises backslashes for its safety
        checks and for deriving the root, but compares the raw
        ``entry.path`` against ``f"{root}/SKILL.md"``. A manifest whose
        paths use backslashes therefore passes the safety checks yet is
        rejected with a misleading "No SKILL.md" error.
        """
        root = WorkspaceService.validate_manifest(
            _manifest(["myskill\\SKILL.md", "myskill\\README.md"]),
            2,
        )
        self.assertEqual(root, "myskill")

    def test_mixed_separators_are_accepted(self) -> None:
        """A manifest may mix both separators."""
        root = WorkspaceService.validate_manifest(
            _manifest(["myskill\\SKILL.md", "myskill/README.md"]),
            2,
        )
        self.assertEqual(root, "myskill")

    def test_missing_skill_md_still_rejected(self) -> None:
        """The fix must not accept a folder without SKILL.md (control)."""
        with self.assertRaises(SkillUploadError):
            WorkspaceService.validate_manifest(
                _manifest(["myskill\\README.md"]),
                1,
            )

    def test_path_traversal_still_rejected(self) -> None:
        """Normalising for SKILL.md must not weaken path safety (control)."""
        with self.assertRaises(SkillUploadError):
            WorkspaceService.validate_manifest(
                _manifest(["pack/../../SKILL.md"]),
                1,
            )
