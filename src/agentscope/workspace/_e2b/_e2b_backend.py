# -*- coding: utf-8 -*-
"""E2B sandbox :class:`SandboxBackend` implementation.

Wraps the E2B SDK's ``commands.run`` and ``files.*`` APIs into the
seven-method :class:`SandboxBackend` protocol so that builtin tools
(Bash, Read, Write, Edit, Grep, Glob) can operate inside an E2B
cloud sandbox transparently.
"""

from __future__ import annotations

import shlex
from typing import Any

from ...tool._builtin._sandbox_backend import ExecResult


class E2BBackend:
    """Backend that delegates to a running E2B sandbox.

    Args:
        sandbox: An ``e2b.AsyncSandbox`` object (must already be
            started / connected).
        workdir: Default working directory for ``exec_shell`` calls
            inside the sandbox.
    """

    def __init__(self, sandbox: Any, workdir: str) -> None:
        self._sandbox = sandbox
        self._workdir = workdir

    # ── exec ───────────────────────────────────────────────────────

    async def exec_shell(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run *command* inside the sandbox via ``commands.run``."""
        from e2b import CommandExitException

        kwargs: dict[str, Any] = {"cwd": cwd or self._workdir}
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            res = await self._sandbox.commands.run(command, **kwargs)
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
        """Read a file from the sandbox via ``files.read``."""
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
        """
        import posixpath

        parent = posixpath.dirname(path)
        if parent:
            await self.exec_shell(f"mkdir -p {shlex.quote(parent)}")
        await self._sandbox.files.write(path, data)

    # ── path introspection ─────────────────────────────────────────

    async def file_exists(self, path: str) -> bool:
        """Check if *path* exists inside the sandbox."""
        result = await self.exec_shell(
            f"test -e {shlex.quote(path)}",
        )
        return result.ok()

    async def is_dir(self, path: str) -> bool:
        """Check if *path* is a directory inside the sandbox."""
        result = await self.exec_shell(
            f"test -d {shlex.quote(path)}",
        )
        return result.ok()

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        """List directory entries inside the sandbox."""
        if recursive:
            result = await self.exec_shell(
                f"find {shlex.quote(path)} -type f",
            )
        else:
            result = await self.exec_shell(
                f"ls -1 {shlex.quote(path)}",
            )
        if not result.ok():
            return []
        raw = result.stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            return []
        return raw.split("\n")

    async def stat_mtime(self, path: str) -> float | None:
        """Return the modification time of *path* inside the sandbox."""
        result = await self.exec_shell(
            f"stat -c %Y {shlex.quote(path)} 2>/dev/null",
        )
        if not result.ok():
            return None
        try:
            return float(
                result.stdout.decode("utf-8", errors="replace").strip(),
            )
        except ValueError:
            return None
