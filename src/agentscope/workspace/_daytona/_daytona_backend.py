# -*- coding: utf-8 -*-
"""Daytona sandbox :class:`BackendBase` implementation.

Wraps Daytona SDK ``process.exec`` and ``fs.*`` APIs into the three
backend primitives (``exec_shell``, ``read_file``, ``write_file``) so
that builtin tools (Bash, Read, Write, Edit, Grep, Glob) can operate
inside a Daytona sandbox transparently. All derived filesystem helpers
(``file_exists``, ``is_dir``, ``list_dir``, ``stat_mtime``,
``delete_path``) are inherited from :class:`BackendBase`, which
implements them via ``exec_shell``.
"""

from __future__ import annotations

import math
import posixpath
import shlex
from typing import Any

from ...tool import BackendBase, ExecResult


class DaytonaBackend(BackendBase):
    """Backend that delegates to a running Daytona sandbox.

    Only the three abstract primitives (``exec_shell``, ``read_file``,
    ``write_file``) are implemented here; the derived filesystem helpers
    are inherited from :class:`BackendBase`.

    Args:
        sandbox (`Any`):
            A Daytona sandbox object (must already be started /
            attached).
        workdir (`str`):
            Default working directory for ``exec_shell`` calls inside
            the sandbox.
    """

    def __init__(self, sandbox: Any, workdir: str) -> None:
        """Initialize the Daytona backend.

        Args:
            sandbox (`Any`):
                A started / attached Daytona sandbox object.
            workdir (`str`):
                Default working directory for ``exec_shell`` calls
                inside the sandbox.
        """
        self._sandbox = sandbox
        self._workdir = workdir

    # ── exec ─────────────────────────────────────────────────────

    async def getcwd(self) -> str:
        """Return the sandbox's default working directory.

        Overrides the base class default (which would shell out to
        ``pwd``) with the cached ``workdir`` supplied at construction,
        avoiding a per-call sandbox round-trip.

        Returns:
            `str`:
                The sandbox's default working directory.
        """
        return self._workdir

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run a program inside the sandbox via ``process.exec``.

        *command* is an argv list. Daytona ``process.exec`` takes one
        shell command line, so argv is POSIX-quoted back into a string
        before dispatch. Callers needing shell features pass
        ``["sh", "-c", line]``.

        Args:
            command (`list[str]`):
                Executable path/name followed by its arguments.
            cwd (`str | None`, optional):
                Working directory inside the sandbox. When ``None`` the
                backend's default ``workdir`` is used.
            timeout (`float | None`, optional):
                Maximum number of seconds to wait. Daytona expects an
                integer timeout, so provided values are rounded up to
                the next integer second.

        Returns:
            `ExecResult`:
                The captured exit code and output. Daytona's SDK exposes
                only ``exit_code`` and ``result`` (no separate stderr
                field), so the command is wrapped with ``2>&1`` to fold
                stderr into ``result`` — otherwise command error output
                would be silently dropped. ``stdout`` therefore carries
                the merged stream and ``stderr`` stays empty for normal
                responses. Transport errors yield an ``exit_code`` of
                ``-1`` with the exception text in ``stderr``.
        """
        # Fold stderr into stdout at the shell level; the SDK lacks a
        # dedicated stderr field, so without this error output is lost.
        command_line = " ".join(shlex.quote(arg) for arg in command) + " 2>&1"
        kwargs: dict[str, Any] = {"cwd": cwd or self._workdir}
        if timeout is not None:
            kwargs["timeout"] = math.ceil(timeout)

        try:
            res = await self._sandbox.process.exec(command_line, **kwargs)
            return ExecResult(
                exit_code=int(res.exit_code),
                stdout=res.result.encode("utf-8"),
                stderr=b"",
            )
        except Exception as e:  # noqa: BLE001
            return ExecResult(
                exit_code=-1,
                stdout=b"",
                stderr=str(e).encode("utf-8"),
            )

    # ── file I/O ─────────────────────────────────────────────────

    async def read_file(self, path: str) -> bytes:
        """Read a file from the sandbox via ``fs.download_file``.

        Args:
            path (`str`):
                Path to the file inside the sandbox.

        Returns:
            `bytes`:
                The raw file contents.

        Raises:
            `FileNotFoundError`:
                If the path does not exist inside the sandbox.
        """
        from daytona import DaytonaNotFoundError

        try:
            data = await self._sandbox.fs.download_file(path)
        except FileNotFoundError:
            raise
        except DaytonaNotFoundError as exc:
            raise FileNotFoundError(
                f"not found in sandbox: {path}",
            ) from exc
        return bytes(data)

    async def write_file(self, path: str, data: bytes) -> None:
        """Write *data* to a file inside the sandbox.

        Creates parent directories via ``exec_shell`` first.

        Args:
            path (`str`):
                Destination path inside the sandbox.
            data (`bytes`):
                The raw bytes to write.
        """
        parent = posixpath.dirname(path)
        if parent:
            await self.exec_shell(["mkdir", "-p", parent])
        await self._sandbox.fs.upload_file(data, path)
