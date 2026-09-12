# -*- coding: utf-8 -*-
"""OpenSandbox :class:`BackendBase` implementation.

Wraps the OpenSandbox SDK's ``commands.run`` and ``files.*`` APIs into
the three backend primitives (``exec_shell``, ``read_file``,
``write_file``) so builtin tools can operate inside an OpenSandbox
sandbox transparently.  All derived filesystem helpers (``file_exists``,
``is_dir``, ``list_dir``, ``stat_mtime``, ``delete_path``) are inherited
from :class:`BackendBase`, which implements them via ``exec_shell``.
"""

from __future__ import annotations

import posixpath
import shlex
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from ...tool import BackendBase, ExecResult

if TYPE_CHECKING:
    from opensandbox.sandbox import Sandbox


class _WriteEntry:
    """Small SDK-compatible write entry used when the SDK type is absent."""

    def __init__(self, path: str, data: bytes, mode: int) -> None:
        self.path = path
        self.data = data
        self.mode = mode


class OpenSandboxBackend(BackendBase):
    """Backend that delegates to a running OpenSandbox sandbox.

    Only the three abstract primitives (``exec_shell``, ``read_file``,
    ``write_file``) are implemented here; the derived filesystem
    helpers are inherited from :class:`BackendBase`.

    Args:
        sandbox (`Sandbox`):
            A started / connected ``opensandbox.sandbox.Sandbox`` object.
        workdir (`str`):
            Default working directory for ``exec_shell`` calls inside the
            sandbox.
    """

    def __init__(self, sandbox: "Sandbox", workdir: str) -> None:
        """Initialize the OpenSandbox backend.

        Args:
            sandbox (`Sandbox`):
                A started / connected ``opensandbox.sandbox.Sandbox``
                object.
            workdir (`str`):
                Default working directory for ``exec_shell`` calls inside
                the sandbox.
        """
        self._sandbox = sandbox
        self._workdir = workdir

    # ---- exec ---------------------------------------------------------

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run a program inside the sandbox via ``commands.run``.

        *command* is an argv list. The OpenSandbox ``commands.run`` API
        expects a single shell command line, so the argv is POSIX-quoted
        back into a string before dispatch. Callers needing shell
        features should pass ``["sh", "-c", line]``.

        Args:
            command (`list[str]`):
                Executable path/name followed by its arguments.
            cwd (`str | None`, optional):
                Working directory inside the sandbox. When ``None`` the
                backend's default ``workdir`` is used.
            timeout (`float | None`, optional):
                Maximum number of seconds to wait. When ``None`` the SDK
                default applies.

        Returns:
            `ExecResult`:
                The captured exit code, stdout, and stderr. A non-zero
                command exit is returned as a normal result; transport
                errors yield an ``exit_code`` of ``-1``.
        """
        command_line = " ".join(shlex.quote(arg) for arg in command)
        opts = self._make_run_opts(cwd=cwd or self._workdir, timeout=timeout)
        try:
            res = await self._sandbox.commands.run(command_line, opts=opts)
            return ExecResult(
                exit_code=int(getattr(res, "exit_code", 0) or 0),
                stdout=self._execution_stream_bytes(res, "stdout"),
                stderr=self._execution_stream_bytes(res, "stderr"),
            )
        except Exception as e:  # noqa: BLE001
            return ExecResult(
                exit_code=-1,
                stdout=b"",
                stderr=str(e).encode("utf-8"),
            )

    # ---- file I/O -----------------------------------------------------

    async def read_file(self, path: str) -> bytes:
        """Read a file from the sandbox via ``files.read_bytes``.

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
        try:
            data = await self._sandbox.files.read_bytes(path)
        except FileNotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001
            # OpenSandbox surfaces missing files through the HTTP
            # transport today (httpx.HTTPStatusError with a 404
            # response). Keep a message fallback for SDK wrappers that
            # do not expose the response object.
            if self._is_not_found_error(exc):
                raise FileNotFoundError(
                    f"not found in OpenSandbox sandbox: {path}",
                ) from exc
            raise
        return data

    async def write_file(self, path: str, data: bytes) -> None:
        """Write raw bytes to a file inside the sandbox.

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

        entry = self._make_write_entry(path, data)
        await self._sandbox.files.write_files([entry])

    @staticmethod
    def _make_write_entry(path: str, data: bytes) -> Any:
        """Build a binary-safe OpenSandbox write entry.

        The implementation prefers the real
        ``opensandbox.models.filesystem.WriteEntry`` type
        when available, but falls back to a tiny local adapter to avoid
        hard dependency on the SDK during tests.

        Args:
            path (`str`):
                Destination path inside the sandbox.
            data (`bytes`):
                Raw bytes to write.

        Returns:
            `Any`:
                A write entry object suitable for ``files.write_files``.
        """
        try:
            from opensandbox.models.filesystem import WriteEntry

            return WriteEntry(path=path, data=data, mode=0o644)
        except Exception:  # noqa: BLE001
            return _WriteEntry(path=path, data=data, mode=0o644)

    @staticmethod
    def _make_run_opts(cwd: str, timeout: float | None) -> Any:
        """Build SDK command options, falling back to a test adapter."""
        try:
            from opensandbox.models.execd import RunCommandOpts

            return RunCommandOpts(
                working_directory=cwd,
                timeout=(
                    timedelta(seconds=timeout) if timeout is not None else None
                ),
            )
        except Exception:  # noqa: BLE001
            return type(
                "RunCommandOpts",
                (),
                {
                    "working_directory": cwd,
                    "timeout": (
                        timedelta(seconds=timeout)
                        if timeout is not None
                        else None
                    ),
                },
            )()

    @classmethod
    def _is_not_found_error(cls, exc: BaseException) -> bool:
        """Return whether an SDK/transport exception means HTTP 404."""
        if cls._status_code(exc) == 404:
            return True

        for chained in (exc.__cause__, exc.__context__):
            if chained is not None and chained is not exc:
                if cls._is_not_found_error(chained):
                    return True

        return "not found" in str(exc).lower()

    @staticmethod
    def _status_code(exc: BaseException) -> int | None:
        """Extract a response/status code from common SDK error shapes."""
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code is None:
            status_code = getattr(exc, "status_code", None)
        if status_code is None:
            return None
        try:
            return int(status_code)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _execution_stream_bytes(res: Any, stream: str) -> bytes:
        """Extract stdout/stderr bytes from real or test execution shapes.

        The real SDK returns an ``Execution`` with accumulated
        ``logs.stdout`` / ``logs.stderr`` entries. Some unit tests use a
        simpler direct ``stdout`` / ``stderr`` shape to mirror E2B-style
        command results, so this helper normalizes both.
        """
        direct = getattr(res, stream, None)
        if direct is not None:
            if isinstance(direct, bytes):
                return direct
            return str(direct).encode("utf-8")

        logs = getattr(res, "logs", None)
        messages = getattr(logs, stream, []) if logs is not None else []
        parts = [str(getattr(msg, "text", msg) or "") for msg in messages]
        text = ""
        for part in parts:
            if text and part:
                previous_is_tight = (
                    not text.endswith(
                        ("\n", "\r"),
                    )
                    and not text[-1].isspace()
                )
                next_is_tight = (
                    not part.startswith(
                        ("\n", "\r"),
                    )
                    and not part[0].isspace()
                )
                if previous_is_tight and next_is_tight:
                    text += "\n"
            text += part
        return text.encode("utf-8")
