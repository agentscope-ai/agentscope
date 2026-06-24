# -*- coding: utf-8 -*-
"""Docker container :class:`BackendBase` implementation.

Wraps the ``aiodocker`` container APIs (``exec``, ``get_archive``,
``put_archive``) into the three backend primitives (``exec_shell``,
``read_file``, ``write_file``) so that builtin tools (Bash, Read,
Write, Edit, Grep, Glob) can operate inside a Docker container
transparently.  All derived filesystem helpers (``file_exists``,
``is_dir``, ``list_dir``, ``stat_mtime``, ``delete_path``) are
inherited from :class:`BackendBase`, which implements them via
``exec_shell``.
"""

from __future__ import annotations

import asyncio
import io
import posixpath
import tarfile
from typing import Any

from ...tool import BackendBase, ExecResult


class DockerBackend(BackendBase):
    """Backend that delegates to a running Docker container.

    Only the three abstract primitives (``exec_shell``, ``read_file``,
    ``write_file``) are implemented here; the derived filesystem helpers
    are inherited from :class:`BackendBase`.

    Args:
        container (`Any`):
            An ``aiodocker`` container object (must already be started).
        workdir (`str`):
            Default working directory for ``exec_shell`` calls inside
            the container.
    """

    def __init__(self, container: Any, workdir: str) -> None:
        """Initialize the Docker backend.

        Args:
            container (`Any`):
                A started ``aiodocker`` container object.
            workdir (`str`):
                Default working directory for ``exec_shell`` calls
                inside the container.
        """
        self._container = container
        self._workdir = workdir

    # ── exec ───────────────────────────────────────────────────────

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run a program directly inside the container.

        *command* is an argv list executed via the container ``exec``
        API without an intervening shell. Callers needing shell features
        wrap their command line as ``["sh", "-c", line]``.

        Args:
            command (`list[str]`):
                Executable path/name followed by its arguments.
            cwd (`str | None`, optional):
                Working directory inside the container. When ``None``
                the backend's default ``workdir`` is used.
            timeout (`float | None`, optional):
                Maximum number of seconds to wait before returning an
                ``exit_code`` of ``-1``. When ``None`` the call waits
                indefinitely.

        Returns:
            `ExecResult`:
                The captured exit code, stdout, and stderr.
        """

        async def _run() -> ExecResult:
            exec_obj = await self._container.exec(
                cmd=command,
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
        """Fetch a file from the container via ``get_archive``.

        Args:
            path (`str`):
                Path to the file inside the container.

        Returns:
            `bytes`:
                The raw file contents.

        Raises:
            `FileNotFoundError`:
                If the path does not exist inside the container.
        """
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
        """Write raw bytes to a file inside the container.

        Creates the parent directory first since ``put_archive``
        requires it to exist.

        Args:
            path (`str`):
                Destination path inside the container.
            data (`bytes`):
                The raw bytes to write.
        """
        parent = posixpath.dirname(path) or "/"
        name = posixpath.basename(path)

        await self.exec_shell(["mkdir", "-p", parent])
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        await self._container.put_archive(parent, buf.getvalue())
