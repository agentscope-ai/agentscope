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
                integer timeout, so provided values are cast to ``int``.

        Returns:
            `ExecResult`:
                The captured exit code, stdout and stderr. Transport
                errors yield an ``exit_code`` of ``-1``.
        """
        command_line = " ".join(shlex.quote(arg) for arg in command)
        kwargs: dict[str, Any] = {"cwd": cwd or self._workdir}
        if timeout is not None:
            kwargs["timeout"] = int(timeout)

        try:
            res = await self._sandbox.process.exec(command_line, **kwargs)
            stdout = _response_stdout(res)
            stderr = _response_stderr(res)
            return ExecResult(
                exit_code=int(getattr(res, "exit_code", None) or 0),
                stdout=stdout.encode("utf-8"),
                stderr=stderr.encode("utf-8"),
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
        try:
            data = await self._sandbox.fs.download_file(path)
        except FileNotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001
            if _looks_not_found(exc):
                raise FileNotFoundError(
                    f"not found in sandbox: {path}",
                ) from exc
            raise
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


# ── SDK response helpers ──────────────────────────────────────────


def _response_stdout(response: Any) -> str:
    """Extract stdout from Daytona's command response variants."""
    stdout = getattr(response, "stdout", None)
    if stdout is not None:
        return str(stdout)
    artifacts = getattr(response, "artifacts", None)
    if artifacts is not None:
        artifact_stdout = getattr(artifacts, "stdout", None)
        if artifact_stdout is not None:
            return str(artifact_stdout)
    result = getattr(response, "result", None)
    return "" if result is None else str(result)


def _response_stderr(response: Any) -> str:
    """Extract stderr when the SDK exposes one."""
    stderr = getattr(response, "stderr", None)
    if stderr is not None:
        return str(stderr)
    props = getattr(response, "additional_properties", None)
    if isinstance(props, dict):
        value = props.get("stderr") or props.get("error")
        if value is not None:
            return str(value)
    return ""


def _looks_not_found(exc: Exception) -> bool:
    """Best-effort mapping for SDK file-not-found errors."""
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    return (
        "notfound" in name
        or "not_found" in name
        or "not found" in msg
        or "404" in msg
    )
