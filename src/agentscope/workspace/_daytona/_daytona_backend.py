# -*- coding: utf-8 -*-
"""Daytona :class:`BackendBase` implementation."""

from __future__ import annotations

import posixpath
import shlex
from typing import Any

from ...tool import BackendBase, ExecResult


class DaytonaBackend(BackendBase):
    """Backend that delegates to a running Daytona sandbox."""

    def __init__(self, sandbox: Any, workdir: str) -> None:
        """Initialize the backend with a started Daytona sandbox."""
        self._sandbox = sandbox
        self._workdir = workdir

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run an argv command through Daytona ``process.exec``."""
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

    async def read_file(self, path: str) -> bytes:
        """Read a file from the sandbox via Daytona ``fs.download_file``."""
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
        """Write bytes via Daytona ``fs.upload_file``."""
        parent = posixpath.dirname(path)
        if parent:
            await self.exec_shell(["mkdir", "-p", parent])
        await self._sandbox.fs.upload_file(data, path)


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
