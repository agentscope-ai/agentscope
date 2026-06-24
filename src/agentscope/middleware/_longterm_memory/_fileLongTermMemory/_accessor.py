# -*- coding: utf-8 -*-
"""Constrained UTF-8 file access over an AgentScope backend.

The adapter only needs a backend for file operations and a workdir for path
resolution. Workspace selection and lifecycle remain middleware concerns.

All caller paths are workdir-relative and traversal is rejected before a
backend operation runs. The adapter owns no backend lifecycle.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from ....tool import BackendBase


class BackendFileAccessor:
    """Constrained UTF-8 file access rooted at a backend workdir."""

    def __init__(self, backend: BackendBase, workdir: str) -> None:
        """Bind the accessor to a backend and filesystem root.

        Args:
            backend:
                Backend used for every file operation.
            workdir:
                Filesystem root below which every LTM path is resolved.
        """
        self._backend = backend
        self._root = workdir.replace("\\", "/").rstrip("/")

    def _resolve(self, path: str) -> str:
        """Resolve and validate one workdir-relative path.

        Absolute paths, drive-qualified Windows paths, empty paths, and parent
        traversal are rejected. The returned path uses POSIX separators so it
        is accepted by local, Docker, and E2B backends.
        """
        raw = path.replace("\\", "/")
        if raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
            raise ValueError(
                f"Expected a workdir-relative path: {path!r}",
            )
        normalized = raw.strip("/")
        pure = PurePosixPath(normalized)
        if not normalized or pure.is_absolute() or ".." in pure.parts:
            raise ValueError(f"Invalid workdir-relative path: {path!r}")
        return f"{self._root}/{pure}"

    async def ensure_dir(self, path: str) -> None:
        """Create an empty directory using backend file primitives only.

        ``BackendBase`` intentionally exposes file writes rather than a public
        mkdir primitive. Writing and deleting a marker creates every parent
        directory while leaving the requested directory empty.
        """
        target = self._resolve(path)
        if await self._backend.is_dir(target):
            return
        marker = f"{target}/.ltm-init"
        await self._backend.write_file(marker, b"")
        await self._backend.delete_path(marker)

    async def exists(self, path: str) -> bool:
        """Return whether a workdir-relative file or directory exists."""
        return await self._backend.file_exists(self._resolve(path))

    async def read_text(self, path: str) -> str:
        """Read a workdir-relative file as strict UTF-8 text."""
        return (await self._backend.read_file(self._resolve(path))).decode(
            "utf-8",
        )

    async def write_text(self, path: str, content: str) -> None:
        """Overwrite a workdir-relative file with UTF-8 text."""
        await self._backend.write_file(
            self._resolve(path),
            content.encode("utf-8"),
        )

    async def stat_mtime(self, path: str) -> float | None:
        """Return a workdir-relative path's modification timestamp."""
        return await self._backend.stat_mtime(self._resolve(path))

    async def list_files(self, path: str, suffix: str) -> list[str]:
        """List immediate child files whose names end with ``suffix``.

        Nested paths returned by a backend are discarded because daily-memory
        discovery is intentionally limited to direct ``YYYY-MM-DD.md`` files.
        """
        directory = self._resolve(path)
        if not await self._backend.is_dir(directory):
            return []
        return sorted(
            name
            for name in await self._backend.list_dir(directory)
            if "/" not in name and "\\" not in name and name.endswith(suffix)
        )
