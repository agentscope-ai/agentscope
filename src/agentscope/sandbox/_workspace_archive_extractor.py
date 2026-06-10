# -*- coding: utf-8 -*-
"""Secure tar archive extraction with Zip Slip guards.

Ported from Java ``WorkspaceArchiveExtractor``.
"""
from __future__ import annotations

import os
import tarfile
from pathlib import Path
from typing import IO

from .._logging import logger


class ArchiveExtractError(Exception):
    """Raised when an archive entry fails security validation."""


class WorkspaceArchiveExtractor:
    """Extracts tar archives safely.

    Defences:
    - Rejects absolute paths (``/etc/passwd``).
    - Rejects ``..`` traversal segments.
    - Rejects null bytes in member names.
    - Verifies the resolved destination is strictly within the canonical root.
    """

    @staticmethod
    def extract_tar_archive(
        dest_root: str | Path,
        tar_stream: IO[bytes],
    ) -> None:
        """Extract a tar stream into *dest_root* with Zip Slip guards.

        Args:
            dest_root: Directory that will receive the extracted files.
            tar_stream: Readable stream of tar data.

        Raises:
            ArchiveExtractError: If a member fails validation.
        """
        dest = Path(dest_root).resolve()
        dest.mkdir(parents=True, exist_ok=True)

        with tarfile.open(fileobj=tar_stream, mode="r:*") as tf:
            members = tf.getmembers()
            for member in members:
                WorkspaceArchiveExtractor._validate_member(member, dest)
                tf.extract(member, path=str(dest), filter="fully_trusted")
            logger.debug(
                "WorkspaceArchiveExtractor: extracted %d members to '%s'",
                len(members),
                dest,
            )

    @staticmethod
    def _validate_member(member: tarfile.TarInfo, dest: Path) -> None:
        name = member.name

        # Null-byte guard
        if "\x00" in name:
            raise ArchiveExtractError(
                f"Archive member contains null byte: {name!r}",
            )

        # Absolute path guard
        if os.path.isabs(name):
            raise ArchiveExtractError(
                f"Archive member has absolute path: {name!r}",
            )

        # ".." traversal guard
        parts = Path(name).parts
        if ".." in parts:
            raise ArchiveExtractError(
                f"Archive member contains '..' traversal: {name!r}",
            )

        # Resolved-path must be under dest
        resolved = (dest / name).resolve()
        try:
            resolved.relative_to(dest)
        except ValueError as exc:
            raise ArchiveExtractError(
                f"Archive member resolves outside destination: "
                f"{name!r} → {resolved}",
            ) from exc
