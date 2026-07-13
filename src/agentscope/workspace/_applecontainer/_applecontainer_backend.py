# -*- coding: utf-8 -*-
"""Apple Container :class:`BackendBase` implementation.

Wraps the ``container`` CLI (``container exec``, ``container cp``)
into the three backend primitives (``exec_shell``, ``read_file``,
``write_file``) so that builtin tools (Bash, Read, Write, Edit, Grep,
Glob) can operate inside an Apple container transparently. All derived
filesystem helpers (``file_exists``, ``is_dir``, ``list_dir``,
``stat_mtime``, ``delete_path``) are inherited from
:class:`BackendBase`, which implements them via ``exec_shell``.
"""

from __future__ import annotations

import asyncio
import os
import posixpath
import shlex
import tempfile

from ...tool import BackendBase, ExecResult


class AppleContainerBackend(BackendBase):
    """Backend that delegates to a running Apple container via the
    ``container`` CLI.

    Only the three abstract primitives (``exec_shell``, ``read_file``,
    ``write_file``) are implemented here; the derived filesystem helpers
    are inherited from :class:`BackendBase`.

    Args:
        container_id (`str`):
            The Apple container identifier (set via ``--name`` at
            creation time).
        workdir (`str`):
            Default working directory for ``exec_shell`` calls inside
            the container.
    """

    def __init__(self, container_id: str, workdir: str) -> None:
        """Initialize the Apple Container backend.

        Args:
            container_id (`str`):
                The container identifier.
            workdir (`str`):
                Default working directory for ``exec_shell`` calls
                inside the container.
        """
        self._container_id = container_id
        self._workdir = workdir

    # ── exec ───────────────────────────────────────────────────────

    async def getcwd(self) -> str:
        """Return the container's default working directory.

        Returns:
            `str`:
                The container's default working directory.
        """
        return self._workdir

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run a program inside the Apple container via
        ``container exec``.

        *command* is an argv list. The Apple container ``exec`` accepts
        arguments directly, so no quoting is needed. Callers needing
        shell features pass ``["sh", "-c", line]``.

        Args:
            command (`list[str]`):
                Executable path/name followed by its arguments.
            cwd (`str | None`, optional):
                Working directory inside the container. When ``None``
                the backend's default ``workdir`` is used.
            timeout (`float | None`, optional):
                Maximum number of seconds to wait. When ``None`` the
                call waits indefinitely.

        Returns:
            `ExecResult`:
                The captured exit code, stdout, and stderr.
        """
        workdir = cwd or self._workdir
        cli_cmd = [
            "container",
            "exec",
            "--workdir",
            workdir,
            self._container_id,
            "--",
        ] + list(command)

        try:
            process = await asyncio.create_subprocess_exec(
                *cli_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                return ExecResult(
                    exit_code=-1,
                    stdout=b"",
                    stderr=b"timed out",
                )
            return ExecResult(
                exit_code=process.returncode or 0,
                stdout=stdout,
                stderr=stderr,
            )
        except FileNotFoundError:
            return ExecResult(
                exit_code=127,
                stdout=b"",
                stderr=b"container CLI not found - is Apple Container "
                b"installed?",
            )
        except OSError as exc:
            return ExecResult(
                exit_code=-1,
                stdout=b"",
                stderr=str(exc).encode("utf-8"),
            )

    # ── file I/O ───────────────────────────────────────────────────

    async def read_file(self, path: str) -> bytes:
        """Read a file from the container via ``container exec cat``.

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
        result = await self.exec_shell(["cat", path])
        if result.exit_code != 0:
            raise FileNotFoundError(
                f"not found in container: {path}\n"
                f"stderr: {result.stderr.decode(errors='replace')}",
            )
        return result.stdout

    async def write_file(self, path: str, data: bytes) -> None:
        """Write *data* to a file inside the container via
        ``container cp``.

        Writes *data* to a host-side temp file, then copies it into the
        container.

        Args:
            path (`str`):
                Destination path inside the container.
            data (`bytes`):
                The raw bytes to write.
        """
        # Write data to a host-side temp file.
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix="as_ws_",
            suffix=".bin",
        )
        try:
            os.write(tmp_fd, data)
            os.close(tmp_fd)

            # Create parent directory inside the container first.
            parent = posixpath.dirname(path)
            if parent and parent != "/":
                await self.exec_shell(
                    ["mkdir", "-p", parent],
                )

            # Copy the temp file into the container.
            container_path = f"{self._container_id}:{path}"
            process = await asyncio.create_subprocess_exec(
                "container",
                "cp",
                tmp_path,
                container_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            if process.returncode != 0:
                raise OSError(
                    f"container cp failed (exit {process.returncode}): "
                    f"{stderr.decode(errors='replace')}",
                )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
