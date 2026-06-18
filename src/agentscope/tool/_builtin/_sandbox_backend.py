# -*- coding: utf-8 -*-
"""Sandbox backend abstraction for builtin tools.

Provides a :class:`SandboxBackend` Protocol that captures the core I/O
primitives shared across all six builtin tools (Bash, Read, Write, Edit,
Grep, Glob).  Each primitive has a single canonical implementation path per
backend:

* :class:`LocalBackend` — default; uses ``asyncio`` subprocesses,
  ``aiofiles``, and ``os.*`` for host-local I/O.  Injected automatically
  when no explicit backend is given.
* ``DockerBackend`` — uses ``aiodocker`` exec / archive APIs.
* ``E2BBackend`` — uses the E2B SDK ``commands`` / ``files`` APIs.

By accepting a ``SandboxBackend`` parameter, each builtin tool can operate
identically in local, Docker, and E2B workspaces without any workspace-
specific branching inside the tool code itself.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import aiofiles

# ── data class ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExecResult:
    """Result of running a shell command via a backend.

    Attributes:
        exit_code: Process exit code.  ``-1`` conventionally indicates
            an internal failure (timeout, connection error, …).
        stdout: Raw bytes captured from standard output.
        stderr: Raw bytes captured from standard error.
    """

    exit_code: int
    stdout: bytes
    stderr: bytes

    def ok(self) -> bool:
        """Return ``True`` iff the command exited with code ``0``."""
        return self.exit_code == 0


# ── protocol ───────────────────────────────────────────────────────────


@runtime_checkable
class SandboxBackend(Protocol):
    """Minimal filesystem + subprocess interface consumed by builtin tools.

    Eight async methods covering shell execution, file I/O, path
    introspection, and deletion.  Any object that duck-types these
    methods can be injected into a builtin tool without explicit
    inheritance.
    """

    async def exec_shell(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Execute ``command`` in a shell and capture output."""

    async def read_file(self, path: str) -> bytes:
        """Read the full contents of ``path`` as raw bytes."""

    async def write_file(self, path: str, data: bytes) -> None:
        """Write ``data`` to ``path``, creating parent directories."""

    async def file_exists(self, path: str) -> bool:
        """Return ``True`` if ``path`` exists (file or directory)."""

    async def is_dir(self, path: str) -> bool:
        """Return ``True`` if ``path`` is an existing directory."""

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        """List entries under ``path``.

        When *recursive* is ``True``, return all files underneath
        ``path`` (like ``find path -type f``).  When ``False``, return
        immediate children (like ``ls -1``).
        """

    async def stat_mtime(self, path: str) -> float | None:
        """Return the modification time of ``path``, or ``None``."""

    async def delete_path(self, path: str) -> None:
        """Delete ``path`` (file or directory tree).

        If ``path`` does not exist the call is a silent no-op (like
        ``rm -rf``).  Implementations must handle both files and
        directories (recursively).
        """


# ── local backend ──────────────────────────────────────────────────────


def _subprocess_creation_kwargs() -> dict[str, Any]:
    """Return platform-specific subprocess creation options."""
    if os.name != "nt":
        return {}

    import subprocess

    return {
        "creationflags": getattr(
            subprocess,
            "CREATE_NO_WINDOW",
            0x08000000,
        ),
    }


class LocalBackend:
    """Host-local :class:`SandboxBackend` implementation.

    Uses ``asyncio.create_subprocess_shell``, ``aiofiles``, and the
    ``os`` module.  This is the default backend injected when no
    explicit one is given to a builtin tool.
    """

    async def exec_shell(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Execute command locally via ``asyncio.create_subprocess_shell``."""
        kwargs = _subprocess_creation_kwargs()
        if cwd is not None:
            kwargs["cwd"] = cwd

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return ExecResult(exit_code=-1, stdout=b"", stderr=b"timed out")

        return ExecResult(
            exit_code=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )

    async def read_file(self, path: str) -> bytes:
        """Read a local file as raw bytes."""
        async with aiofiles.open(path, mode="rb") as f:
            return await f.read()

    async def write_file(self, path: str, data: bytes) -> None:
        """Write *data* to a local file, creating parent dirs."""
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        async with aiofiles.open(path, mode="wb") as f:
            await f.write(data)

    async def file_exists(self, path: str) -> bool:
        """Check if a local path exists."""
        return os.path.exists(path)

    async def is_dir(self, path: str) -> bool:
        """Check if a local path is a directory."""
        return os.path.isdir(path)

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        """List local directory entries."""
        if recursive:
            results: list[str] = []
            for root, _dirs, files in os.walk(path):
                for f in files:
                    results.append(os.path.join(root, f))
            return results
        return os.listdir(path)

    async def stat_mtime(self, path: str) -> float | None:
        """Return the modification time of a local file."""
        try:
            return os.stat(path).st_mtime
        except (OSError, FileNotFoundError):
            return None

    async def delete_path(self, path: str) -> None:
        """Delete a local file or directory tree.

        No-op if *path* does not exist.
        """
        if not os.path.exists(path):
            return
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
