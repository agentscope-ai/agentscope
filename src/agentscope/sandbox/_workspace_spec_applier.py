# -*- coding: utf-8 -*-
"""Materialises a :class:`WorkspaceSpec` into a local directory.

Ported from Java ``WorkspaceSpecApplier``.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ._workspace_spec import (
    WorkspaceSpec,
    WorkspaceEntry,
    FileEntry,
    DirEntry,
    LocalFileEntry,
    LocalDirEntry,
    BindMountEntry,
    GitRepoEntry,
    WorkspaceProjectionEntry,
)
from .._logging import logger


class WorkspaceSpecApplier:
    """Applies a :class:`WorkspaceSpec` to a local filesystem path.

    Usage::

        applier = WorkspaceSpecApplier()
        applier.apply(spec, "/tmp/workspace")

    The applier is stateless and safe to reuse across calls.
    """

    def apply(
        self,
        spec: WorkspaceSpec,
        dest_root: str | Path,
        *,
        only_ephemeral: bool = False,
    ) -> None:
        """Materialise *spec* under *dest_root*.

        Args:
            spec: The desired workspace layout.
            dest_root: Host-side directory that will receive the files.
            only_ephemeral: When ``True``, only entries whose
                ``ephemeral=True`` flag is set are applied. This is used
                when resuming a sandbox from a snapshot so that transient
                config is refreshed without overwriting user data restored
                from the snapshot.
        """
        dest = Path(dest_root)
        for rel_path, entry in spec.entries.items():
            if only_ephemeral and not entry.ephemeral:
                continue
            self._apply_entry(dest, rel_path, entry)

    def _apply_entry(
        self,
        dest: Path,
        rel_path: str,
        entry: WorkspaceEntry,
    ) -> None:
        target = dest / rel_path
        # Security: ensure target stays under dest
        try:
            target.resolve().relative_to(dest.resolve())
        except ValueError as exc:
            logger.warning(
                "WorkspaceSpecApplier: path '%s' escapes root '%s', skipping",
                rel_path,
                dest,
            )
            return

        if isinstance(entry, FileEntry):
            self._write_file(target, entry)
        elif isinstance(entry, DirEntry):
            self._write_dir(target, entry)
        elif isinstance(entry, LocalFileEntry):
            self._copy_local_file(target, entry)
        elif isinstance(entry, LocalDirEntry):
            self._copy_local_dir(target, entry)
        elif isinstance(entry, BindMountEntry):
            # Bind mounts are handled by the container backend at runtime,
            # not by the applier.
            logger.debug(
                "WorkspaceSpecApplier: skipping BindMountEntry '%s'",
                rel_path,
            )
        elif isinstance(entry, GitRepoEntry):
            logger.warning(
                "WorkspaceSpecApplier: GitRepoEntry '%s' not yet implemented",
                rel_path,
            )
        elif isinstance(entry, WorkspaceProjectionEntry):
            # Projections are applied inside Sandbox.start() via archive
            # hydration, not by the regular applier.
            logger.debug(
                "WorkspaceSpecApplier: skipping WorkspaceProjectionEntry '%s'",
                rel_path,
            )
        else:
            logger.warning(
                "WorkspaceSpecApplier: unknown entry type %s for '%s'",
                type(entry).__name__,
                rel_path,
            )

    def _write_file(self, target: Path, entry: FileEntry) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(entry.content, encoding=entry.encoding)
        logger.debug("WorkspaceSpecApplier: wrote file '%s'", target)

    def _write_dir(self, target: Path, entry: DirEntry) -> None:
        target.mkdir(parents=True, exist_ok=True)
        for child_name, child_entry in entry.children.items():
            self._apply_entry(target, child_name, child_entry)
        logger.debug("WorkspaceSpecApplier: wrote dir '%s'", target)

    def _copy_local_file(self, target: Path, entry: LocalFileEntry) -> None:
        src = Path(entry.source_path)
        if not src.exists():
            logger.warning(
                "WorkspaceSpecApplier: local file '%s' not found, skipping",
                src,
            )
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(target))
        logger.debug("WorkspaceSpecApplier: copied '%s' → '%s'", src, target)

    def _copy_local_dir(self, target: Path, entry: LocalDirEntry) -> None:
        src = Path(entry.source_path)
        if not src.exists():
            logger.warning(
                "WorkspaceSpecApplier: local dir '%s' not found, skipping",
                src,
            )
            return
        if target.exists():
            shutil.rmtree(str(target))
        shutil.copytree(str(src), str(target))
        logger.debug("WorkspaceSpecApplier: copied tree '%s' → '%s'", src, target)
