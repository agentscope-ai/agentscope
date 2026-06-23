# -*- coding: utf-8 -*-
"""Constrained UTF-8 file access over AgentScope workspace backends.

The LTM store deliberately depends on this small adapter rather than on
``LocalWorkspace`` / ``DockerWorkspace`` / ``E2BWorkspace`` directly. Every
workspace exposes the same :class:`BackendBase` file primitives, allowing the
store and middleware to remain backend-agnostic.

All caller paths are workspace-relative and traversal is rejected before a
backend operation runs. The adapter owns no lifecycle and never caches a
backend object; the workspace remains responsible for initialization and
shutdown.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from ....tool import BackendBase

if TYPE_CHECKING:
    from ....workspace import WorkspaceBase


class WorkspaceFileAccessor:
    """Constrained UTF-8 file access rooted in one workspace.

    The workspace backend is resolved for every operation. Docker and E2B
    workspaces replace their backend when they reconnect, so retaining a
    backend instance in this adapter would leave it pointing at stale runtime
    resources.
    """

    def __init__(self, workspace: "WorkspaceBase") -> None:
        """Bind the accessor to one workspace root.

        Args:
            workspace:
                An initialized AgentScope workspace. Its ``workdir`` is the
                root below which every LTM path is resolved.
        """
        self._workspace = workspace
        self._root = workspace.workdir.replace("\\", "/").rstrip("/")

    @property
    def backend(self) -> BackendBase:
        """Return the workspace's current backend.

        Workspace implementations have not exposed a public backend property
        yet. This is the only compatibility point that touches ``_backend``;
        it can switch to a public property without changing the store.
        """
        backend = getattr(self._workspace, "_backend", None)
        if not isinstance(backend, BackendBase):
            raise RuntimeError(
                f"{type(self._workspace).__name__} has no active backend. "
                "Initialize the workspace before using file-based LTM.",
            )
        return backend

    def _resolve(self, path: str) -> str:
        """Resolve and validate one workspace-relative path.

        Absolute paths, drive-qualified Windows paths, empty paths, and parent
        traversal are rejected. The returned path uses POSIX separators so it
        is accepted by local, Docker, and E2B backends.
        """
        raw = path.replace("\\", "/")
        if raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw):
            raise ValueError(f"Expected a workspace-relative path: {path!r}")
        normalized = raw.strip("/")
        pure = PurePosixPath(normalized)
        if not normalized or pure.is_absolute() or ".." in pure.parts:
            raise ValueError(f"Invalid workspace-relative path: {path!r}")
        return f"{self._root}/{pure}"

    async def ensure_dir(self, path: str) -> None:
        """Create an empty directory using backend file primitives only.

        ``BackendBase`` intentionally exposes file writes rather than a public
        mkdir primitive. Writing and deleting a marker creates every parent
        directory while leaving the requested directory empty.
        """
        target = self._resolve(path)
        if await self.backend.is_dir(target):
            return
        marker = f"{target}/.ltm-init"
        await self.backend.write_file(marker, b"")
        await self.backend.delete_path(marker)

    async def exists(self, path: str) -> bool:
        """Return whether a workspace-relative file or directory exists."""
        return await self.backend.file_exists(self._resolve(path))

    async def read_text(self, path: str) -> str:
        """Read a workspace-relative file as strict UTF-8 text."""
        return (await self.backend.read_file(self._resolve(path))).decode(
            "utf-8",
        )

    async def write_text(self, path: str, content: str) -> None:
        """Overwrite a workspace-relative file with UTF-8 text."""
        await self.backend.write_file(
            self._resolve(path),
            content.encode("utf-8"),
        )

    async def list_files(self, path: str, suffix: str) -> list[str]:
        """List immediate child files whose names end with ``suffix``.

        Nested paths returned by a backend are discarded because daily-memory
        discovery is intentionally limited to direct ``YYYY-MM-DD.md`` files.
        """
        directory = self._resolve(path)
        if not await self.backend.is_dir(directory):
            return []
        return sorted(
            name
            for name in await self.backend.list_dir(directory)
            if "/" not in name
            and "\\" not in name
            and name.endswith(suffix)
        )
