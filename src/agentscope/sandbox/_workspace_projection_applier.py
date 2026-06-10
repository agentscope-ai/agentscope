# -*- coding: utf-8 -*-
"""Builds a deterministic tar payload + SHA-256 hash for workspace projections.

Ported from Java ``WorkspaceProjectionApplier``.
"""
from __future__ import annotations

import hashlib
import io
import os
import tarfile
from dataclasses import dataclass
from pathlib import Path

from ._workspace_spec import WorkspaceSpec, WorkspaceProjectionEntry


@dataclass(frozen=True)
class ProjectionPayload:
    """Result of building a workspace projection.

    Attributes:
        hash: SHA-256 hex digest of the tar payload.
        tar_bytes: The raw tar archive bytes.
        file_count: Number of files included in the archive.
    """

    hash: str
    tar_bytes: bytes
    file_count: int


class WorkspaceProjectionApplier:
    """Builds a projection tar from :class:`WorkspaceProjectionEntry` items.

    The content hash allows the sandbox lifecycle to **skip re-applying
    unchanged projections** across calls.
    """

    @staticmethod
    def build(spec: WorkspaceSpec) -> ProjectionPayload:
        """Collect all :class:`WorkspaceProjectionEntry` items in *spec* and
        build a single tar payload.

        Args:
            spec: The workspace spec to scan.

        Returns:
            :class:`ProjectionPayload` with hash, tar bytes, and file count.
        """
        buf = io.BytesIO()
        file_count = 0

        with tarfile.open(fileobj=buf, mode="w") as tf:
            for _rel_path, entry in spec.entries.items():
                if not isinstance(entry, WorkspaceProjectionEntry):
                    continue
                file_count += WorkspaceProjectionApplier._add_projection(
                    tf,
                    entry,
                )

        tar_bytes = buf.getvalue()
        digest = hashlib.sha256(tar_bytes).hexdigest()
        return ProjectionPayload(
            hash=digest,
            tar_bytes=tar_bytes,
            file_count=file_count,
        )

    @staticmethod
    def _add_projection(
        tf: tarfile.TarInfo,
        entry: WorkspaceProjectionEntry,
    ) -> int:
        """Add files from a single projection entry to the tar archive.

        Returns:
            Number of files added.
        """
        source_root = Path(entry.source_root).resolve()
        if not source_root.exists():
            return 0

        include_roots = entry.include_roots
        if not include_roots:
            include_roots = ["."]

        count = 0
        for rel in include_roots:
            src = (source_root / rel).resolve()
            if not src.exists():
                continue
            if src.is_file():
                arcname = str(Path(rel).as_posix())
                tf.add(str(src), arcname=arcname)
                count += 1
            elif src.is_dir():
                for root, _dirs, files in os.walk(str(src)):
                    for fname in files:
                        full = Path(root) / fname
                        arc = full.relative_to(source_root).as_posix()
                        tf.add(str(full), arcname=arc)
                        count += 1
        return count
