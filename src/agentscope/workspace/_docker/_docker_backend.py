# -*- coding: utf-8 -*-
"""Docker container :class:`SandboxBackend` implementation.

Wraps the ``aiodocker`` container APIs (``exec``, ``get_archive``,
``put_archive``) into the seven-method :class:`SandboxBackend`
protocol so that builtin tools (Bash, Read, Write, Edit, Grep, Glob)
can operate inside a Docker container transparently.
"""

from __future__ import annotations

import asyncio
import io
import posixpath
import shlex
import tarfile
from typing import Any

from ...tool._builtin._sandbox_backend import ExecResult


class DockerBackend:
    """Backend that delegates to a running Docker container.

    Args:
        container: An ``aiodocker`` container object (must already
            be started).
        workdir: Default working directory for ``exec_shell`` calls
            inside the container.
    """

    def __init__(self, container: Any, workdir: str) -> None:
        self._container = container
        self._workdir = workdir

    # ── exec ───────────────────────────────────────────────────────

    async def exec_shell(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run ``sh -c <command>`` inside the container."""

        async def _run() -> ExecResult:
            exec_obj = await self._container.exec(
                cmd=["sh", "-c", command],
                workdir=cwd or self._workdir,
            )
            stdout_parts: list[bytes] = []
            stderr_parts: list[bytes] = []
            async with exec_obj.start() as stream:
                while True:
                    msg = await stream.read_out()
                    if msg is None:
                        break
                    if msg.stream == 1:
                        stdout_parts.append(msg.data)
                    else:
                        stderr_parts.append(msg.data)
            inspect = await exec_obj.inspect()
            code = inspect.get("ExitCode", -1)
            if code is None:
                code = -1
            return ExecResult(
                exit_code=int(code),
                stdout=b"".join(stdout_parts),
                stderr=b"".join(stderr_parts),
            )

        if timeout is None:
            return await _run()
        try:
            return await asyncio.wait_for(_run(), timeout=timeout)
        except asyncio.TimeoutError:
            return ExecResult(
                exit_code=-1,
                stdout=b"",
                stderr=b"timed out",
            )

    # ── file I/O ───────────────────────────────────────────────────

    async def read_file(self, path: str) -> bytes:
        """Fetch a file from the container via ``get_archive``."""
        from aiodocker import exceptions as aiodocker_exceptions

        try:
            tar = await self._container.get_archive(path)
        except aiodocker_exceptions.DockerError as exc:
            if exc.status == 404:
                raise FileNotFoundError(
                    f"not found in container: {path}",
                ) from exc
            raise

        try:
            for member in tar.getmembers():
                if member.isfile():
                    f = tar.extractfile(member)
                    if f:
                        return f.read()
        finally:
            tar.close()
        raise FileNotFoundError(f"not found in container: {path}")

    async def write_file(self, path: str, data: bytes) -> None:
        """Write raw bytes to a file inside the container."""
        parent = posixpath.dirname(path) or "/"
        name = posixpath.basename(path)

        await self.exec_shell(f"mkdir -p {shlex.quote(parent)}")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        await self._container.put_archive(parent, buf.getvalue())

    # ── path introspection ─────────────────────────────────────────

    async def file_exists(self, path: str) -> bool:
        """Check if *path* exists inside the container."""
        result = await self.exec_shell(
            f"test -e {shlex.quote(path)}",
        )
        return result.ok()

    async def is_dir(self, path: str) -> bool:
        """Check if *path* is a directory inside the container."""
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
        """List directory entries inside the container."""
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
        """Return the modification time of *path* inside the container."""
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
