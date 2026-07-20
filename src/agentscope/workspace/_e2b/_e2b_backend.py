# -*- coding: utf-8 -*-
"""E2B sandbox :class:`BackendBase` implementation.

Wraps the E2B SDK's ``commands.run`` and ``files.*`` APIs into the
three backend primitives (``exec_shell``, ``read_file``,
``write_file``) so that builtin tools (Bash, Read, Write, Edit, Grep,
Glob) can operate inside an E2B cloud sandbox transparently.  All
derived filesystem helpers (``file_exists``, ``is_dir``, ``list_dir``,
``stat_mtime``, ``delete_path``) are inherited from
:class:`BackendBase`, which implements them via ``exec_shell``.
"""

from __future__ import annotations

import posixpath
import shlex
from typing import Any

from ...tool import BackendBase, ExecResult


class E2BBackend(BackendBase):
    """Backend that delegates to a running E2B sandbox.

    Only the three abstract primitives (``exec_shell``, ``read_file``,
    ``write_file``) are implemented here; the derived filesystem helpers
    are inherited from :class:`BackendBase`.

    Args:
        sandbox (`Any`):
            An ``e2b.AsyncSandbox`` object (must already be started /
            connected).
        workdir (`str`):
            Default working directory for ``exec_shell`` calls inside
            the sandbox.
    """

    def __init__(self, sandbox: Any, workdir: str) -> None:
        """Initialize the E2B backend.

        Args:
            sandbox (`Any`):
                A started / connected ``e2b.AsyncSandbox`` object.
            workdir (`str`):
                Default working directory for ``exec_shell`` calls
                inside the sandbox.
        """
        self._sandbox = sandbox
        self._workdir = workdir

    # ── exec ───────────────────────────────────────────────────────

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
        """Run a program inside the sandbox via ``commands.run``.

        *command* is an argv list. The E2B ``commands.run`` API takes a
        single shell command line, so the argv is POSIX-quoted back into
        a string before dispatch (the sandbox is always Linux). Callers
        needing shell features pass ``["sh", "-c", line]``.

        Args:
            command (`list[str]`):
                Executable path/name followed by its arguments.
            cwd (`str | None`, optional):
                Working directory inside the sandbox. When ``None`` the
                backend's default ``workdir`` is used.
            timeout (`float | None`, optional):
                Maximum number of seconds to wait. When ``None`` the
                SDK default applies.

        Returns:
            `ExecResult`:
                The captured exit code, stdout, and stderr. A non-zero
                command exit is reported as a normal result; transport
                errors yield an ``exit_code`` of ``-1``.
        """
        from e2b import CommandExitException

        command_line = " ".join(shlex.quote(arg) for arg in command)
        kwargs: dict[str, Any] = {"cwd": cwd or self._workdir}
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            res = await self._sandbox.commands.run(command_line, **kwargs)
            return ExecResult(
                exit_code=int(res.exit_code or 0),
                stdout=(res.stdout or "").encode("utf-8"),
                stderr=(res.stderr or "").encode("utf-8"),
            )
        except CommandExitException as e:
            return ExecResult(
                exit_code=int(e.exit_code or 1),
                stdout=(e.stdout or "").encode("utf-8"),
                stderr=(e.stderr or "").encode("utf-8"),
            )
        except Exception as e:  # noqa: BLE001
            return ExecResult(
                exit_code=-1,
                stdout=b"",
                stderr=str(e).encode("utf-8"),
            )

    # ── file I/O ───────────────────────────────────────────────────

    async def read_file(self, path: str) -> bytes:
        """Read a file from the sandbox via ``files.read``.

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
        from e2b import FileNotFoundException

        try:
            data = await self._sandbox.files.read(path, format="bytes")
        except FileNotFoundException as exc:
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
        await self._sandbox.files.write(path, data)
