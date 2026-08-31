# -*- coding: utf-8 -*-
"""Backend abstraction for builtin tools.

Provides a :class:`BackendBase` abstract base class that captures the
core I/O primitives shared across all six builtin tools (Bash, Read,
Write, Edit, Grep, Glob).

Every backend implements exactly **three** abstract primitives whose
mechanism genuinely differs per environment:

* :meth:`BackendBase.exec_shell` — run a program from an argv list
  (no shell; callers needing shell features wrap with ``sh -c``).
* :meth:`BackendBase.read_file` — read raw bytes.
* :meth:`BackendBase.write_file` — write raw bytes.

All remaining filesystem operations (``file_exists``, ``is_dir``,
``list_dir``, ``stat_mtime``, ``delete_path``) are derived on the base
class from ``exec_shell`` and work out-of-the-box for any remote
backend.  A backend that has a cheaper native path (e.g.
:class:`LocalBackend` using ``os.*``) simply overrides them.

Concrete implementations:

* :class:`LocalBackend` — default; uses ``asyncio`` subprocesses,
  ``aiofiles``, and ``os.*`` for host-local I/O.  Injected automatically
  when no explicit backend is given.
* ``DockerBackend`` — uses ``aiodocker`` exec / archive APIs.
* ``E2BBackend`` — uses the E2B SDK ``commands`` / ``files`` APIs.

By accepting a ``BackendBase`` parameter, each builtin tool can
operate identically in local, Docker, and E2B workspaces without any
workspace-specific branching inside the tool code itself.
"""

from __future__ import annotations

import asyncio
import os
import posixpath
import shlex
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import ModuleType
from typing import Any, AsyncIterator

import aiofiles

# Chunk size for streamed reads. Large enough that a big file does not
# turn into thousands of awaits, small enough to stay off the heap.
DEFAULT_READ_CHUNK_SIZE = 1024 * 1024

# One NUL-terminated record per entry, for the shell-based ``scandir``
# and ``stat``. The name goes last because it is the only field that
# may itself contain a tab, so a bounded split keeps it whole.
_FIND_ENTRY_FORMAT = "%Y\\t%s\\t%T@\\t%f\\0"

# ── data class ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExecResult:
    """Result of running a shell command via a backend.

    Attributes:
        exit_code: Process exit code.  ``-1`` conventionally indicates
            an internal failure (timeout, connection error, …).
        stdout: Raw bytes captured from standard output.
        stderr: Raw bytes captured from standard error.
    """

    exit_code: int
    stdout: bytes
    stderr: bytes

    def ok(self) -> bool:
        """Whether the command exited successfully.

        Returns:
            `bool`:
                ``True`` iff the command exited with code ``0``.
        """
        return self.exit_code == 0


@dataclass(frozen=True, slots=True)
class DirEntry:
    """One entry from :meth:`BackendBase.scandir`.

    Mirrors :class:`os.DirEntry`: the metadata comes from the same
    directory read as the name, so a listing costs one round trip
    instead of one per entry plus one per attribute.

    Attributes:
        name: The entry's base name, without any leading directory.
        is_dir: Whether the entry is a directory, following symlinks.
        size_bytes: Size in bytes of a file. Always ``None`` for a
            directory — its on-disk size says nothing a caller wants —
            and ``None`` when it could not be determined (a broken
            symlink, a vanished entry).
        mtime: Modification time as a POSIX timestamp, or ``None``
            for the same reasons.
    """

    name: str
    is_dir: bool
    size_bytes: int | None = None
    mtime: float | None = None


# ── helpers ────────────────────────────────────────────────────────────


def _normalize_newlines(text: str) -> str:
    """Normalize Windows/old-Mac line endings to ``\\n``.

    Converts ``\\r\\n`` (Windows) and lone ``\\r`` (classic Mac) to a
    single ``\\n``.  Builtin tools read files as raw bytes (so binary
    payloads survive intact); when the bytes are decoded as text for
    line-based caching, editing, or matching, the line endings must be
    normalized so that content written on Windows behaves identically to
    content written on POSIX.

    Args:
        text (`str`):
            Decoded file contents.

    Returns:
        `str`:
            The text with all line endings collapsed to ``\\n``.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


# ── base class ─────────────────────────────────────────────────────────


class BackendBase(ABC):
    """Filesystem + subprocess interface consumed by builtin tools.

    Subclasses must implement three abstract primitives — ``exec_shell``,
    ``read_file``, and ``write_file`` — which are the only operations
    whose mechanism genuinely differs per environment.  The remaining
    filesystem helpers are implemented here on top of ``exec_shell`` and
    work for any backend whose shell is POSIX-like; a backend with a
    cheaper native path may override them (see :class:`LocalBackend`).
    """

    #: Path-manipulation module whose semantics match the backend's
    #: environment.  Used by :meth:`join_path`, :meth:`dirname`,
    #: :meth:`isabs`, :meth:`normpath`, and :meth:`abspath` to ensure
    #: correct behavior when the host OS and the backend OS differ
    #: (e.g. a Windows host driving a Linux Docker container).
    #:
    #: Defaults to :mod:`posixpath`, which is correct for any backend
    #: whose environment is Linux/macOS (Docker, E2B, …).  Subclasses
    #: targeting a different environment override this attribute, e.g.
    #: :class:`LocalBackend` sets it to :mod:`os.path`, and a future
    #: Windows-container backend would set it to :mod:`ntpath`.
    #:
    #: .. important::
    #:
    #:     Only **pure string operations** on this module are safe to
    #:     call (``join``, ``split``, ``dirname``, ``basename``,
    #:     ``normpath``, ``isabs``, ``splitext``, ``splitdrive``, …).
    #:
    #:     Do **not** call functions that touch the filesystem or
    #:     environment variables — ``exists``, ``isfile``, ``isdir``,
    #:     ``getmtime``, ``realpath``, ``expanduser``, ``expandvars``,
    #:     and parameterless ``abspath`` — because those read the
    #:     **host** process's filesystem / ``$HOME`` / ``cwd``, which
    #:     is meaningless (and a silent bug) for remote backends.  Use
    #:     the async I/O methods on the backend instead
    #:     (:meth:`file_exists`, :meth:`is_dir`, :meth:`stat_mtime`,
    #:     …) or the :meth:`abspath` wrapper below, which requires an
    #:     explicit ``cwd`` argument.
    _path_module: ModuleType = posixpath

    #: The operating system family of the backend's **environment**,
    #: following the :data:`os.name` convention (``"posix"`` or
    #: ``"nt"``).  Like :attr:`_path_module`, this describes where the
    #: commands actually run, not the host process, so tools can pick
    #: the right shell (``/bin/sh`` vs ``cmd.exe``) when a Windows host
    #: drives a Linux sandbox.  Defaults to ``"posix"``; subclasses
    #: targeting Windows environments override it.
    os_name: str = "posix"

    # ── path manipulation helpers (pure string ops) ────────────────

    def join_path(self, path: str, *paths: str) -> str:
        """Join one or more path components using the backend's separator.

        Args:
            path (`str`):
                The first path component.
            *paths (`str`):
                Additional components to join onto ``path``.

        Returns:
            `str`:
                The joined path, using the backend environment's path
                separator.
        """
        return self._path_module.join(path, *paths)

    def dirname(self, path: str) -> str:
        """Return the directory component of ``path``.

        Args:
            path (`str`):
                A path inside the backend's environment.

        Returns:
            `str`:
                Everything up to (but not including) the last path
                separator. Empty string if ``path`` has no separator.
        """
        return self._path_module.dirname(path)

    def basename(self, path: str) -> str:
        """Return the final component of ``path``.

        Args:
            path (`str`):
                A path inside the backend's environment.

        Returns:
            `str`:
                Everything after the last path separator. Empty string
                if ``path`` ends with a separator.
        """
        return self._path_module.basename(path)

    def isabs(self, path: str) -> bool:
        """Return ``True`` if ``path`` is absolute in the backend.

        Args:
            path (`str`):
                A path inside the backend's environment.

        Returns:
            `bool`:
                ``True`` iff ``path`` is absolute under the backend
                environment's path semantics.
        """
        return self._path_module.isabs(path)

    def normpath(self, path: str) -> str:
        """Normalize ``path`` (collapse ``..``, ``.``, duplicate seps).

        Pure string operation — does not touch the filesystem.

        Args:
            path (`str`):
                A path inside the backend's environment.

        Returns:
            `str`:
                The normalized path.
        """
        return self._path_module.normpath(path)

    def abspath(self, path: str, *, cwd: str) -> str:
        """Return an absolute, normalized version of ``path``.

        Unlike :func:`os.path.abspath`, this helper **never** reads
        the host process's working directory: when ``path`` is
        relative it is joined with the explicitly supplied ``cwd``,
        which must itself be a path that is meaningful inside the
        backend's environment.  This avoids the silent bug where the
        host's ``os.getcwd()`` leaks into paths that will actually be
        used on a remote backend.

        Args:
            path (`str`):
                A path inside the backend's environment.
            cwd (`str`):
                Directory to resolve a relative ``path`` against.
                Ignored when ``path`` is already absolute.

        Returns:
            `str`:
                An absolute, normalized path.
        """
        if self._path_module.isabs(path):
            return self._path_module.normpath(path)
        return self._path_module.normpath(
            self._path_module.join(cwd, path),
        )

    # ── abstract primitives ────────────────────────────────────────

    @abstractmethod
    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run a program directly from an argument vector.

        *command* is an executable followed by its arguments — it is
        **not** passed through a shell, so callers never have to quote
        or escape arguments and there is no platform-specific quoting
        bug. This makes the primitive portable to Windows, where POSIX
        single-quote escaping (``shlex.quote``) is not understood by
        ``cmd.exe``.

        Callers that genuinely need shell features (pipes, redirects,
        ``&&``) must wrap their command line explicitly, e.g.
        ``["/bin/sh", "-c", command_line]``.

        Args:
            command (`list[str]`):
                Executable path/name followed by its arguments.
            cwd (`str | None`, optional):
                Working directory to run the command in. When ``None``
                the backend's default working directory is used.
            timeout (`float | None`, optional):
                Maximum number of seconds to wait. When ``None`` the
                call waits indefinitely. On timeout the result carries
                an ``exit_code`` of ``-1``.

        Returns:
            `ExecResult`:
                The captured exit code, stdout, and stderr.
        """

    @abstractmethod
    async def read_file(self, path: str) -> bytes:
        """Read the full contents of ``path`` as raw bytes.

        Args:
            path (`str`):
                Path to the file inside the backend's environment.

        Returns:
            `bytes`:
                The raw file contents.
        """

    @abstractmethod
    async def write_file(self, path: str, data: bytes) -> None:
        """Write ``data`` to ``path``, creating parent directories.

        Args:
            path (`str`):
                Destination path inside the backend's environment.
            data (`bytes`):
                The raw bytes to write.
        """

    async def write_stream(
        self,
        path: str,
        stream: AsyncIterator[bytes],
    ) -> None:
        """Write a byte stream to ``path``, creating parent directories.

        The default buffers the whole stream and delegates to
        :meth:`write_file`, so peak memory is the payload size; only
        backends that override this (``LocalBackend``,
        ``BubblewrapBackend``, ``K8sBackend``) are constant-memory.
        Callers handling untrusted input must cap the payload
        regardless.

        Args:
            path (`str`):
                Destination path inside the backend's environment.
            stream (`AsyncIterator[bytes]`):
                The chunks to write, in order.
        """
        chunks = [chunk async for chunk in stream]
        await self.write_file(path, b"".join(chunks))

    async def read_stream(
        self,
        path: str,
        chunk_size: int = DEFAULT_READ_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        """Read ``path`` as a byte stream.

        The default reads the whole file through :meth:`read_file` and
        re-slices it, so peak memory is the file size; only backends
        that override this (``LocalBackend``) are constant-memory.
        Overriding requires incremental access to ``exec_shell``
        stdout, which the remote backends do not expose today.

        Args:
            path (`str`):
                Path to the file inside the backend's environment.
            chunk_size (`int`, defaults to 1 MiB):
                The size of each yielded chunk.

        Yields:
            `bytes`:
                Successive chunks of the file, in order.
        """
        data = await self.read_file(path)
        for start in range(0, len(data), chunk_size):
            yield data[start : start + chunk_size]

    # ── derived filesystem ops (shell-based defaults) ──────────────

    async def getcwd(self) -> str:
        """Return the backend environment's current working directory.

        This is the directory that bare ``exec_shell`` invocations
        (those with ``cwd=None``) execute in.  Tools should call this
        — instead of :func:`os.getcwd` — whenever they need a default
        path that is meaningful inside the backend, because the host
        process's cwd is meaningless for remote backends
        (Docker / E2B).

        The default implementation runs ``pwd`` via :meth:`exec_shell`,
        which works for any POSIX-like backend.  Backends with cheaper
        native access (e.g. :class:`LocalBackend`, or remote backends
        that already track their workdir) should override it.

        Returns:
            `str`:
                The backend's current working directory.
        """
        result = await self.exec_shell(["pwd"])
        return result.stdout.decode("utf-8", errors="replace").strip()

    async def expanduser(self, path: str) -> str:
        """Expand a leading ``~`` / ``~/`` to the backend's home directory.

        Tools should call this — instead of :func:`os.path.expanduser`
        — whenever they need to expand ``~`` in a path that lives
        inside the backend's environment, because the host process's
        ``$HOME`` is meaningless for remote backends.

        The default implementation queries ``$HOME`` via
        :meth:`exec_shell` (POSIX-only).  Only the leading ``~`` /
        ``~/foo`` form is expanded; ``~user/...`` is not supported by
        the default and is returned unchanged.  Backends with cheaper
        native access should override (e.g. :class:`LocalBackend`).

        Args:
            path (`str`):
                A path inside the backend's environment, possibly
                starting with ``~``.

        Returns:
            `str`:
                ``path`` with a leading ``~`` / ``~/`` expanded.  If
                ``path`` does not start with ``~``, or starts with
                ``~user`` (unsupported), it is returned unchanged.
        """
        if not path or path[0] != "~":
            return path
        # ``~user/...`` form — not supported by the default impl.
        if len(path) > 1 and path[1] not in ("/", self._path_module.sep):
            return path
        result = await self.exec_shell(["printenv", "HOME"])
        home = result.stdout.decode("utf-8", errors="replace").strip()
        if not home:
            return path
        return home + path[1:]

    async def file_exists(self, path: str) -> bool:
        """Return ``True`` if ``path`` exists (file or directory).

        Args:
            path (`str`):
                Path to test inside the backend's environment.

        Returns:
            `bool`:
                ``True`` if the path exists, ``False`` otherwise.
        """
        result = await self.exec_shell(["test", "-e", path])
        return result.ok()

    async def is_dir(self, path: str) -> bool:
        """Return ``True`` if ``path`` is an existing directory.

        Args:
            path (`str`):
                Path to test inside the backend's environment.

        Returns:
            `bool`:
                ``True`` if the path is an existing directory.
        """
        result = await self.exec_shell(["test", "-d", path])
        return result.ok()

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        """List entries under ``path``.

        Output is NUL-delimited (``find -print0`` / ``-printf '%f\\0'``)
        and split on ``\\0`` so that file names containing spaces or
        newlines are handled correctly.  ``find -printf`` is a GNU
        extension; backends running on non-GNU userlands should override
        this method.

        Args:
            path (`str`):
                Directory to list inside the backend's environment.
            recursive (`bool`, optional):
                When ``True``, return all files underneath ``path`` as
                paths (like ``find path -type f``). When ``False``
                (default), return the immediate children's base names
                (like ``os.listdir``).

        Returns:
            `list[str]`:
                The matched entries, or an empty list if ``path`` does
                not exist or cannot be listed.
        """
        if recursive:
            command = ["find", path, "-type", "f", "-print0"]
        else:
            command = [
                "find",
                path,
                "-mindepth",
                "1",
                "-maxdepth",
                "1",
                "-printf",
                "%f\\0",
            ]
        result = await self.exec_shell(command)
        if not result.ok():
            return []
        return [
            part.decode("utf-8", errors="surrogateescape")
            for part in result.stdout.split(b"\0")
            if part
        ]

    async def scandir(self, path: str) -> list[DirEntry]:
        """List one directory level with each entry's metadata.

        The type, size and mtime come back from the same ``find`` run
        as the names, so listing a directory of N entries costs one
        round trip rather than 1 + 3N. Prefer this over
        :meth:`list_dir` plus per-entry calls whenever the metadata is
        wanted; ``find -printf`` is a GNU extension, so backends on
        non-GNU userlands should override it.

        ``%Y`` follows symlinks, matching what :meth:`is_dir` reports;
        the size and mtime describe the link itself, which only
        differs for the link's own few bytes.

        Args:
            path (`str`):
                Directory to list inside the backend's environment.

        Returns:
            `list[DirEntry]`:
                The immediate children, or an empty list if ``path``
                does not exist or cannot be listed.
        """
        return await self._find_entries(
            [
                "find",
                path,
                "-mindepth",
                "1",
                "-maxdepth",
                "1",
                "-printf",
                _FIND_ENTRY_FORMAT,
            ],
        )

    async def stat(self, path: str) -> DirEntry | None:
        """Return one path's type, size and mtime in a single call.

        The single-path counterpart of :meth:`scandir`, the way
        :func:`os.stat` sits beside :func:`os.scandir`. Prefer it over
        asking :meth:`file_exists`, :meth:`is_dir` and
        :meth:`stat_mtime` in turn, which is three round trips for what
        one command answers.

        Args:
            path (`str`):
                Path to stat inside the backend's environment.

        Returns:
            `DirEntry | None`:
                The entry, or ``None`` if ``path`` does not exist.
                ``name`` is the base name, as :meth:`scandir` reports
                it.
        """
        entries = await self._find_entries(
            ["find", path, "-maxdepth", "0", "-printf", _FIND_ENTRY_FORMAT],
            skip_unresolvable=True,
        )
        return entries[0] if entries else None

    async def _find_entries(
        self,
        command: list[str],
        *,
        skip_unresolvable: bool = False,
    ) -> list[DirEntry]:
        """Parse the records a ``_FIND_ENTRY_FORMAT`` run prints.

        Args:
            command (`list[str]`):
                The ``find`` argv, already carrying the format.
            skip_unresolvable (`bool`, defaults to ``False``):
                Drop entries whose target could not be followed — a
                dangling symlink, most often. :meth:`stat` wants this
                (``os.stat`` follows the link and fails, and a caller
                about to read the file must not be told it is there);
                :meth:`scandir` does not, since ``os.scandir`` lists a
                broken link like any other name.

        Returns:
            `list[DirEntry]`:
                One entry per record, or an empty list when ``find``
                failed (a missing path, most often).
        """
        result = await self.exec_shell(command)
        if not result.ok():
            return []

        entries: list[DirEntry] = []
        for record in result.stdout.split(b"\0"):
            if not record:
                continue
            fields = record.decode("utf-8", errors="surrogateescape").split(
                "\t",
                3,
            )
            if len(fields) != 4:
                continue
            kind, raw_size, raw_mtime, name = fields
            try:
                size: int | None = int(raw_size)
            except ValueError:
                size = None
            try:
                mtime: float | None = float(raw_mtime)
            except ValueError:
                mtime = None
            # ``%Y`` reports N/L/? when it cannot follow the link. The
            # size and mtime it still prints describe the link itself,
            # which would read as a real 21-byte file; ``os.scandir``
            # reports nothing there, so neither does this.
            if kind in ("N", "L", "?"):
                if skip_unresolvable:
                    continue
                size, mtime = None, None
            is_dir = kind == "d"
            entries.append(
                DirEntry(
                    name=name,
                    is_dir=is_dir,
                    size_bytes=None if is_dir else size,
                    mtime=mtime,
                ),
            )
        return entries

    async def stat_mtime(self, path: str) -> float | None:
        """Return the modification time of ``path``, or ``None``.

        Tries GNU ``stat -c %Y`` first and falls back to BSD
        ``stat -f %m`` so the same call works across coreutils and
        BSD/macOS userlands.  The two attempts are combined with ``||``,
        so this default wraps a ``sh -c`` script; backends without a
        POSIX shell (e.g. :class:`LocalBackend`) override it.

        Args:
            path (`str`):
                Path to stat inside the backend's environment.

        Returns:
            `float | None`:
                The modification time as a POSIX timestamp, or ``None``
                if the path does not exist or cannot be stat'd.
        """
        quoted = shlex.quote(path)
        script = (
            f"stat -c %Y {quoted} 2>/dev/null || "
            f"stat -f %m {quoted} 2>/dev/null"
        )
        result = await self.exec_shell(["sh", "-c", script])
        if not result.ok():
            return None
        try:
            return float(
                result.stdout.decode("utf-8", errors="replace").strip(),
            )
        except ValueError:
            return None

    async def delete_path(self, path: str) -> None:
        """Delete ``path`` (file or directory tree).

        If ``path`` does not exist the call is a silent no-op (like
        ``rm -rf``).  Handles both files and directories (recursively).

        Args:
            path (`str`):
                Path to delete inside the backend's environment.
        """
        await self.exec_shell(["rm", "-rf", path])


# ── local backend ──────────────────────────────────────────────────────


def _subprocess_creation_kwargs() -> dict[str, Any]:
    """Return platform-specific subprocess creation options.

    Returns:
        `dict[str, Any]`:
            Extra keyword arguments for ``create_subprocess_shell``.
            Empty on POSIX; on Windows it sets ``creationflags`` to
            suppress a console window.
    """
    if os.name != "nt":
        return {}

    import subprocess

    return {
        "creationflags": getattr(
            subprocess,
            "CREATE_NO_WINDOW",
            0x08000000,
        ),
    }


class LocalBackend(BackendBase):
    """Host-local :class:`BackendBase` implementation.

    Uses ``asyncio.create_subprocess_exec``, ``aiofiles``, and the
    ``os`` module.  This is the default backend injected when no
    explicit one is given to a builtin tool.  Commands are spawned
    directly from their argument vector (no shell), which avoids the
    POSIX-vs-``cmd.exe`` quoting mismatch and makes the backend work on
    Windows.  The derived filesystem helpers are overridden with native
    ``os.*`` calls — faster and more robust than shelling out, and
    portable to Windows where ``test`` / ``find`` / ``stat`` are
    unavailable.
    """

    # Use the host OS's path semantics (Windows or POSIX) instead of
    # the base class default (``posixpath``), so path helpers behave
    # correctly when running on a Windows host.
    _path_module = os.path

    # The local backend runs commands on the host, so its environment
    # OS is the host OS.
    os_name = os.name

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run a program via ``asyncio.create_subprocess_exec``.

        The program is spawned directly from *command* without an
        intervening shell, so no argument quoting is required and the
        same code path works on POSIX and Windows.

        Args:
            command (`list[str]`):
                Executable path/name followed by its arguments.
            cwd (`str | None`, optional):
                Working directory for the subprocess. When ``None`` the
                current process working directory is used.
            timeout (`float | None`, optional):
                Maximum number of seconds to wait before the process is
                killed and an ``exit_code`` of ``-1`` is returned.

        Returns:
            `ExecResult`:
                The captured exit code, stdout, and stderr. If the
                executable cannot be found or spawned, ``exit_code`` is
                ``127`` (matching a shell's "command not found"), with
                the OS error message on stderr.
        """
        kwargs = _subprocess_creation_kwargs()
        if cwd is not None:
            kwargs["cwd"] = cwd

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs,
            )
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            # The executable could not be found or spawned. A shell would
            # have returned 127 ("command not found"); mirror that so
            # callers see a normal non-zero ExecResult instead of an
            # exception.
            return ExecResult(
                exit_code=127,
                stdout=b"",
                stderr=str(exc).encode("utf-8"),
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return ExecResult(exit_code=-1, stdout=b"", stderr=b"timed out")

        return ExecResult(
            exit_code=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )

    async def read_file(self, path: str) -> bytes:
        """Read a local file as raw bytes.

        Args:
            path (`str`):
                Path to the local file.

        Returns:
            `bytes`:
                The raw file contents.
        """
        async with aiofiles.open(path, mode="rb") as f:
            return await f.read()

    async def write_file(self, path: str, data: bytes) -> None:
        """Write *data* to a local file, creating parent dirs.

        Args:
            path (`str`):
                Destination path on the local filesystem.
            data (`bytes`):
                The raw bytes to write.
        """
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        async with aiofiles.open(path, mode="wb") as f:
            await f.write(data)

    async def write_stream(
        self,
        path: str,
        stream: AsyncIterator[bytes],
    ) -> None:
        """Write a byte stream to a local file, chunk by chunk.

        Args:
            path (`str`):
                Destination path on the local filesystem.
            stream (`AsyncIterator[bytes]`):
                The chunks to write, in order.
        """
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        async with aiofiles.open(path, mode="wb") as f:
            async for chunk in stream:
                await f.write(chunk)

    async def read_stream(
        self,
        path: str,
        chunk_size: int = DEFAULT_READ_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        """Read a local file chunk by chunk, never holding it whole.

        Args:
            path (`str`):
                Path to the local file.
            chunk_size (`int`, defaults to 1 MiB):
                The size of each yielded chunk.

        Yields:
            `bytes`:
                Successive chunks of the file, in order.
        """
        async with aiofiles.open(path, mode="rb") as f:
            while chunk := await f.read(chunk_size):
                yield chunk

    async def getcwd(self) -> str:
        """Return the host process's current working directory.

        Returns:
            `str`:
                ``os.getcwd()`` — avoids spawning a ``pwd`` subprocess.
        """
        return os.getcwd()

    async def expanduser(self, path: str) -> str:
        """Expand ``~`` using the host process's ``$HOME``.

        Args:
            path (`str`):
                A local path, possibly starting with ``~``.

        Returns:
            `str`:
                ``os.path.expanduser(path)`` — avoids spawning a
                subprocess.
        """
        return os.path.expanduser(path)

    async def file_exists(self, path: str) -> bool:
        """Check if a local path exists.

        Args:
            path (`str`):
                Path to test.

        Returns:
            `bool`:
                ``True`` if the path exists.
        """
        return os.path.exists(path)

    async def is_dir(self, path: str) -> bool:
        """Check if a local path is a directory.

        Args:
            path (`str`):
                Path to test.

        Returns:
            `bool`:
                ``True`` if the path is an existing directory.
        """
        return os.path.isdir(path)

    async def list_dir(
        self,
        path: str,
        *,
        recursive: bool = False,
    ) -> list[str]:
        """List local directory entries.

        Mirrors the base contract using native ``os`` calls.

        Args:
            path (`str`):
                Directory to list.
            recursive (`bool`, optional):
                When ``True``, return file paths underneath ``path``
                (``os.walk``). When ``False`` (default), return the
                immediate children's base names (``os.listdir``).

        Returns:
            `list[str]`:
                The matched entries.
        """
        if recursive:
            results: list[str] = []
            for root, _dirs, files in os.walk(path):
                for f in files:
                    results.append(os.path.join(root, f))
            return results
        return os.listdir(path)

    async def scandir(self, path: str) -> list[DirEntry]:
        """List a local directory with each entry's metadata.

        ``os.scandir`` carries the type in the directory record itself,
        so this is cheaper than ``os.listdir`` followed by a ``stat``
        per entry — the same reason the base class batches its ``find``.

        Args:
            path (`str`):
                Directory to list.

        Returns:
            `list[DirEntry]`:
                The immediate children, or an empty list if ``path``
                does not exist or cannot be listed.
        """
        entries: list[DirEntry] = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    # A symlink can break, or the entry can vanish,
                    # between the listing and the stat.
                    try:
                        is_dir = entry.is_dir()
                        stat = entry.stat()
                        size, mtime = stat.st_size, stat.st_mtime
                    except OSError:
                        is_dir, size, mtime = False, None, None
                    entries.append(
                        DirEntry(
                            name=entry.name,
                            is_dir=is_dir,
                            size_bytes=None if is_dir else size,
                            mtime=mtime,
                        ),
                    )
        except OSError:
            return []
        return entries

    async def stat(self, path: str) -> DirEntry | None:
        """Return one local path's type, size and mtime.

        Args:
            path (`str`):
                Path to stat.

        Returns:
            `DirEntry | None`:
                The entry, or ``None`` if ``path`` does not exist or
                cannot be stat'd.
        """
        try:
            # Follows symlinks, as ``os.DirEntry.stat`` does by default.
            info = os.stat(path)
        except OSError:
            return None
        is_dir = os.path.isdir(path)
        return DirEntry(
            name=os.path.basename(path),
            is_dir=is_dir,
            size_bytes=None if is_dir else info.st_size,
            mtime=info.st_mtime,
        )

    async def stat_mtime(self, path: str) -> float | None:
        """Return the modification time of a local file.

        Args:
            path (`str`):
                Path to stat.

        Returns:
            `float | None`:
                The modification time as a POSIX timestamp, or ``None``
                if the path does not exist or cannot be stat'd.
        """
        try:
            return os.stat(path).st_mtime
        except (OSError, FileNotFoundError):
            return None

    async def delete_path(self, path: str) -> None:
        """Delete a local file or directory tree.

        No-op if *path* does not exist.

        Args:
            path (`str`):
                Path to delete.
        """
        if not os.path.exists(path):
            return
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
