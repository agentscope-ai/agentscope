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
import stat
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

    async def getcwd(self) -> str:
        """Return the container's default working directory.

        Overrides the base class default (which would shell out to
        ``pwd``) with the cached ``workdir`` supplied at construction,
        avoiding a per-call container ``exec`` round-trip.

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

    async def _get_write_metadata(
        self,
        path: str,
    ) -> tuple[int, int, int] | None:
        """Read access bits and numeric ownership of an existing regular file.

        Prefer stat to avoid downloading the old contents. Images without
        a compatible stat command can still provide metadata via Docker's
        archive API. Do not follow the final symlink: put_archive replaces it.
        """
        from aiodocker import exceptions as aiodocker_exceptions

        try:
            result = await self.exec_shell(
                ["stat", "-c", "%f %u %g", "--", path],
            )
        except aiodocker_exceptions.DockerError:
            result = None

        if result is not None and result.ok():
            try:
                mode_text, uid_text, gid_text = result.stdout.split()
                mode = int(mode_text, 16)
                uid, gid = int(uid_text), int(gid_text)
            except ValueError:
                pass
            else:
                known_types = (
                    stat.S_IFREG,
                    stat.S_IFDIR,
                    stat.S_IFLNK,
                    stat.S_IFCHR,
                    stat.S_IFBLK,
                    stat.S_IFIFO,
                    stat.S_IFSOCK,
                )
                if (
                    0 <= mode <= 0xFFFF
                    and stat.S_IFMT(mode) in known_types
                    and uid >= 0
                    and gid >= 0
                ):
                    if stat.S_ISREG(mode):
                        return mode & 0o777, uid, gid
                    return None

        try:
            archive = await self._container.get_archive(path)
        except aiodocker_exceptions.DockerError as exc:
            if exc.status == 404:
                return None
            raise

        with archive:
            member = archive.next()
            if member is None or member.name != posixpath.basename(path):
                raise OSError(f"Invalid metadata archive for {path}")
            if member.isfile():
                return member.mode & 0o777, member.uid, member.gid
        return None

    async def write_file(self, path: str, data: bytes) -> None:
        """Write raw bytes to a file inside the container.

        Creates the parent directory first since ``put_archive``
        requires it to exist. Existing regular files keep their ordinary
        access permissions and numeric ownership. Special permission bits
        are cleared, and new files use the archive defaults (mode 0644).

        Args:
            path (`str`):
                Destination path inside the container.
            data (`bytes`):
                The raw bytes to write.
        """
        parent = posixpath.dirname(path) or "/"
        name = posixpath.basename(path)

        await self.exec_shell(["mkdir", "-p", parent])
        # Docker cleans archive paths lexically and resolves relative paths
        # from the container root, independently of the exec working directory.
        metadata = await self._get_write_metadata(
            posixpath.normpath(posixpath.join("/", parent, name)),
        )
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            if metadata is not None:
                info.mode, info.uid, info.gid = metadata
            tf.addfile(info, io.BytesIO(data))
        await self._container.put_archive(parent, buf.getvalue())
