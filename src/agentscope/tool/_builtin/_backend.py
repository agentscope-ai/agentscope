# -*- coding: utf-8 -*-
"""Backend abstraction for builtin tools.

Provides a :class:`BackendBase` abstract base class that captures the
core I/O primitives shared across all six builtin tools (Bash, Read,
Write, Edit, Grep, Glob).

Every backend implements exactly **three** abstract primitives whose
mechanism genuinely differs per environment:

* :meth:`BackendBase.exec_shell` — run a shell command.
* :meth:`BackendBase.read_file` — read raw bytes.
* :meth:`BackendBase.write_file` — write raw bytes.

All remaining filesystem operations (``file_exists``, ``is_dir``,
``list_dir``, ``stat_mtime``, ``delete_path``) are derived on the base
class from ``exec_shell`` and work out-of-the-box for any remote
backend.  A backend that has a cheaper native path (e.g.
:class:`LocalBackend` using ``os.*``) simply overrides them.

Concrete implementations:

* :class:`LocalBackend` — default; uses ``asyncio`` subprocesses,
  ``aiofiles``, and ``os.*`` for host-local I/O.  Injected automatically
  when no explicit backend is given.
* ``DockerBackend`` — uses ``aiodocker`` exec / archive APIs.
* ``E2BBackend`` — uses the E2B SDK ``commands`` / ``files`` APIs.

By accepting a ``BackendBase`` parameter, each builtin tool can
operate identically in local, Docker, and E2B workspaces without any
workspace-specific branching inside the tool code itself.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

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
        """Whether the command exited successfully.

        Returns:
            `bool`:
                ``True`` iff the command exited with code ``0``.
        """
        return self.exit_code == 0


# ── base class ─────────────────────────────────────────────────────────


class BackendBase(ABC):
    """Filesystem + subprocess interface consumed by builtin tools.

    Subclasses must implement three abstract primitives — ``exec_shell``,
    ``read_file``, and ``write_file`` — which are the only operations
    whose mechanism genuinely differs per environment.  The remaining
    filesystem helpers are implemented here on top of ``exec_shell`` and
    work for any backend whose shell is POSIX-like; a backend with a
    cheaper native path may override them (see :class:`LocalBackend`).
    """

    # ── abstract primitives ────────────────────────────────────────

    @abstractmethod
    async def exec_shell(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Execute ``command`` in a shell and capture its output.

        Args:
            command (`str`):
                The shell command line to run.
            cwd (`str | None`, optional):
                Working directory to run the command in. When ``None``
                the backend's default working directory is used.
            timeout (`float | None`, optional):
                Maximum number of seconds to wait. When ``None`` the
                call waits indefinitely. On timeout the result carries
                an ``exit_code`` of ``-1``.

        Returns:
            `ExecResult`:
                The captured exit code, stdout, and stderr.
        """

    @abstractmethod
    async def read_file(self, path: str) -> bytes:
        """Read the full contents of ``path`` as raw bytes.

        Args:
            path (`str`):
                Path to the file inside the backend's environment.

        Returns:
            `bytes`:
                The raw file contents.
        """

    @abstractmethod
    async def write_file(self, path: str, data: bytes) -> None:
        """Write ``data`` to ``path``, creating parent directories.

        Args:
            path (`str`):
                Destination path inside the backend's environment.
            data (`bytes`):
                The raw bytes to write.
        """

    # ── derived filesystem ops (shell-based defaults) ──────────────

    async def file_exists(self, path: str) -> bool:
        """Return ``True`` if ``path`` exists (file or directory).

        Args:
            path (`str`):
                Path to test inside the backend's environment.

        Returns:
            `bool`:
                ``True`` if the path exists, ``False`` otherwise.
        """
        result = await self.exec_shell(f"test -e {shlex.quote(path)}")
        return result.ok()

    async def is_dir(self, path: str) -> bool:
        """Return ``True`` if ``path`` is an existing directory.

        Args:
            path (`str`):
                Path to test inside the backend's environment.

        Returns:
            `bool`:
                ``True`` if the path is an existing directory.
        """
        result = await self.exec_shell(f"test -d {shlex.quote(path)}")
        return result.ok()

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        """List entries under ``path``.

        Output is NUL-delimited (``find -print0`` / ``-printf '%f\\0'``)
        and split on ``\\0`` so that file names containing spaces or
        newlines are handled correctly.  ``find -printf`` is a GNU
        extension; backends running on non-GNU userlands should override
        this method.

        Args:
            path (`str`):
                Directory to list inside the backend's environment.
            recursive (`bool`, optional):
                When ``True``, return all files underneath ``path`` as
                paths (like ``find path -type f``). When ``False``
                (default), return the immediate children's base names
                (like ``os.listdir``).

        Returns:
            `list[str]`:
                The matched entries, or an empty list if ``path`` does
                not exist or cannot be listed.
        """
        if recursive:
            command = f"find {shlex.quote(path)} -type f -print0"
        else:
            command = (
                f"find {shlex.quote(path)} -mindepth 1 -maxdepth 1 "
                f"-printf '%f\\0'"
            )
        result = await self.exec_shell(command)
        if not result.ok():
            return []
        return [
            part.decode("utf-8", errors="surrogateescape")
            for part in result.stdout.split(b"\0")
            if part
        ]

    async def stat_mtime(self, path: str) -> float | None:
        """Return the modification time of ``path``, or ``None``.

        Tries GNU ``stat -c %Y`` first and falls back to BSD
        ``stat -f %m`` so the same call works across coreutils and
        BSD/macOS userlands.

        Args:
            path (`str`):
                Path to stat inside the backend's environment.

        Returns:
            `float | None`:
                The modification time as a POSIX timestamp, or ``None``
                if the path does not exist or cannot be stat'd.
        """
        quoted = shlex.quote(path)
        command = (
            f"stat -c %Y {quoted} 2>/dev/null || "
            f"stat -f %m {quoted} 2>/dev/null"
        )
        result = await self.exec_shell(command)
        if not result.ok():
            return None
        try:
            return float(
                result.stdout.decode("utf-8", errors="replace").strip(),
            )
        except ValueError:
            return None

    async def delete_path(self, path: str) -> None:
        """Delete ``path`` (file or directory tree).

        If ``path`` does not exist the call is a silent no-op (like
        ``rm -rf``).  Handles both files and directories (recursively).

        Args:
            path (`str`):
                Path to delete inside the backend's environment.
        """
        await self.exec_shell(f"rm -rf {shlex.quote(path)}")


# ── local backend ──────────────────────────────────────────────────────


def _subprocess_creation_kwargs() -> dict[str, Any]:
    """Return platform-specific subprocess creation options.

    Returns:
        `dict[str, Any]`:
            Extra keyword arguments for ``create_subprocess_shell``.
            Empty on POSIX; on Windows it sets ``creationflags`` to
            suppress a console window.
    """
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


class LocalBackend(BackendBase):
    """Host-local :class:`BackendBase` implementation.

    Uses ``asyncio.create_subprocess_shell``, ``aiofiles``, and the
    ``os`` module.  This is the default backend injected when no
    explicit one is given to a builtin tool.  The derived filesystem
    helpers are overridden with native ``os.*`` calls — faster and more
    robust than shelling out, and portable to Windows where ``test`` /
    ``find`` / ``stat`` are unavailable.
    """

    async def exec_shell(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Execute a command via ``asyncio.create_subprocess_shell``.

        Args:
            command (`str`):
                The shell command line to run.
            cwd (`str | None`, optional):
                Working directory for the subprocess. When ``None`` the
                current process working directory is used.
            timeout (`float | None`, optional):
                Maximum number of seconds to wait before the process is
                killed and an ``exit_code`` of ``-1`` is returned.

        Returns:
            `ExecResult`:
                The captured exit code, stdout, and stderr.
        """
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
        """Read a local file as raw bytes.

        Args:
            path (`str`):
                Path to the local file.

        Returns:
            `bytes`:
                The raw file contents.
        """
        async with aiofiles.open(path, mode="rb") as f:
            return await f.read()

    async def write_file(self, path: str, data: bytes) -> None:
        """Write *data* to a local file, creating parent dirs.

        Args:
            path (`str`):
                Destination path on the local filesystem.
            data (`bytes`):
                The raw bytes to write.
        """
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        async with aiofiles.open(path, mode="wb") as f:
            await f.write(data)

    async def file_exists(self, path: str) -> bool:
        """Check if a local path exists.

        Args:
            path (`str`):
                Path to test.

        Returns:
            `bool`:
                ``True`` if the path exists.
        """
        return os.path.exists(path)

    async def is_dir(self, path: str) -> bool:
        """Check if a local path is a directory.

        Args:
            path (`str`):
                Path to test.

        Returns:
            `bool`:
                ``True`` if the path is an existing directory.
        """
        return os.path.isdir(path)

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        """List local directory entries.

        Mirrors the base contract using native ``os`` calls.

        Args:
            path (`str`):
                Directory to list.
            recursive (`bool`, optional):
                When ``True``, return file paths underneath ``path``
                (``os.walk``). When ``False`` (default), return the
                immediate children's base names (``os.listdir``).

        Returns:
            `list[str]`:
                The matched entries.
        """
        if recursive:
            results: list[str] = []
            for root, _dirs, files in os.walk(path):
                for f in files:
                    results.append(os.path.join(root, f))
            return results
        return os.listdir(path)

    async def stat_mtime(self, path: str) -> float | None:
        """Return the modification time of a local file.

        Args:
            path (`str`):
                Path to stat.

        Returns:
            `float | None`:
                The modification time as a POSIX timestamp, or ``None``
                if the path does not exist or cannot be stat'd.
        """
        try:
            return os.stat(path).st_mtime
        except (OSError, FileNotFoundError):
            return None

    async def delete_path(self, path: str) -> None:
        """Delete a local file or directory tree.

        No-op if *path* does not exist.

        Args:
            path (`str`):
                Path to delete.
        """
        if not os.path.exists(path):
            return
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
