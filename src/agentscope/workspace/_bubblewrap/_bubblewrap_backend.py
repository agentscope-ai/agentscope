# -*- coding: utf-8 -*-
"""Bubblewrap :class:`BackendBase` implementation.

The backend launches each command through ``bwrap`` with a stable host
directory mounted at ``/workspace`` and a stable host temp directory
mounted at ``/tmp``.  Those two writable mounts are enough for the shared
workspace implementation, gateway request shims, skills, and offload data.
"""

from __future__ import annotations

import asyncio
import os
import posixpath
import signal
from pathlib import PurePosixPath
from typing import Any

from ...tool import BackendBase, ExecResult
from ._constants import SANDBOX_CACHE_DIR, SANDBOX_TMPDIR, SANDBOX_WORKDIR


class BubblewrapBackend(BackendBase):
    """Backend that executes commands inside Bubblewrap sandboxes.

    Args:
        host_workdir (`str`):
            Host directory mounted read-write at ``/workspace``.
        host_tmpdir (`str`):
            Host directory mounted read-write at ``/tmp``.
        host_cache_dir (`str | None`, optional):
            Host directory mounted at ``/tmp/.agentscope-cache`` for package
            manager caches. Isolation depends on whether callers provide a
            workspace-private or shared host directory. It must not overlap
            ``host_workdir`` or ``host_tmpdir``, or resolve through a
            symbolic-link root.
        workdir (`str`, optional):
            Default sandbox working directory.
        share_net (`bool`, optional):
            Keep host networking visible inside the sandbox. When
            ``False``, the network namespace created by ``--unshare-all``
            remains isolated.
        env (`dict[str, str] | None`, optional):
            Extra environment variables for sandboxed processes.
    """

    def __init__(
        self,
        *,
        host_workdir: str,
        host_tmpdir: str,
        host_cache_dir: str | None = None,
        workdir: str = SANDBOX_WORKDIR,
        share_net: bool = True,
        env: dict[str, str] | None = None,
    ) -> None:
        if not host_workdir.strip():
            raise ValueError("host_workdir must not be empty.")
        if not host_tmpdir.strip():
            raise ValueError("host_tmpdir must not be empty.")
        if host_cache_dir is not None and not host_cache_dir.strip():
            raise ValueError("host_cache_dir must not be empty.")
        host_workdir_path = os.path.abspath(host_workdir)
        host_tmpdir_path = os.path.abspath(host_tmpdir)
        workdir_created = not os.path.lexists(host_workdir_path)
        tmpdir_created = not os.path.lexists(host_tmpdir_path)
        os.makedirs(host_workdir_path, mode=0o700, exist_ok=True)
        os.makedirs(host_tmpdir_path, mode=0o700, exist_ok=True)
        if workdir_created:
            os.chmod(host_workdir_path, 0o700)
        if tmpdir_created:
            os.chmod(host_tmpdir_path, 0o700)
        self._host_workdir = os.path.realpath(host_workdir_path)
        self._host_tmpdir = os.path.realpath(host_tmpdir_path)
        self._host_workdir_identity = self._directory_identity(
            self._host_workdir,
            label="host_workdir",
        )
        self._host_tmpdir_identity = self._directory_identity(
            self._host_tmpdir,
            label="host_tmpdir",
        )
        mount_sources: list[tuple[str, str]] = [
            ("host_workdir", self._host_workdir),
            ("host_tmpdir", self._host_tmpdir),
        ]
        self._host_cache_dir: str | None = None
        self._host_cache_identity: tuple[int, int] | None = None
        cache_dir: str | None = None
        if host_cache_dir is not None:
            cache_dir = os.path.abspath(host_cache_dir)
            if os.path.lexists(cache_dir) and os.path.islink(cache_dir):
                raise ValueError("host_cache_dir must not be a symbolic link.")
            cache_realpath = os.path.realpath(cache_dir)
            self._host_cache_dir = cache_realpath
            mount_sources.append(("host_cache_dir", cache_realpath))
        for index, (left_name, left_path) in enumerate(mount_sources):
            for right_name, right_path in mount_sources[index + 1 :]:
                if self._paths_overlap(left_path, right_path):
                    raise ValueError(
                        f"{left_name} must not overlap {right_name}.",
                    )
        if cache_dir is not None:
            cache_created = not os.path.lexists(cache_dir)
            os.makedirs(cache_dir, mode=0o700, exist_ok=True)
            if cache_created:
                os.chmod(cache_dir, 0o700)
            if not os.path.isdir(cache_dir):
                raise ValueError("host_cache_dir must be a directory.")
            assert self._host_cache_dir is not None
            self._host_cache_identity = self._directory_identity(
                self._host_cache_dir,
                label="host_cache_dir",
            )
        self._workdir = workdir
        self._share_net = share_net
        self._env = dict(env or {})

    async def getcwd(self) -> str:
        """Return the backend's default working directory."""
        return self._workdir

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run an argv list inside a fresh Bubblewrap process."""
        if not command:
            return ExecResult(
                exit_code=127,
                stdout=b"",
                stderr=b"empty command",
            )

        try:
            process = await self.start_process(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
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
            await self._terminate_process_tree(process, grace=1.0)
            return ExecResult(exit_code=-1, stdout=b"", stderr=b"timed out")
        except BaseException:
            await asyncio.shield(
                self._terminate_process_tree(process, grace=1.0),
            )
            raise

        return ExecResult(
            exit_code=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )

    async def start_process(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        stdin: Any = asyncio.subprocess.DEVNULL,
        stdout: Any = asyncio.subprocess.DEVNULL,
        stderr: Any = asyncio.subprocess.DEVNULL,
    ) -> asyncio.subprocess.Process:
        """Start a long-running command inside Bubblewrap.

        Used by :class:`BubblewrapWorkspace` to keep the MCP gateway as a
        tracked subprocess instead of daemonizing it inside the sandbox.
        """
        argv = self._bwrap_argv(command, cwd=cwd)
        kwargs: dict[str, Any] = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            **kwargs,
        )

    @staticmethod
    async def _terminate_process_tree(
        process: asyncio.subprocess.Process,
        *,
        grace: float,
    ) -> None:
        """Terminate a process group, falling back to the process itself."""
        if process.returncode is not None:
            return

        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                try:
                    process.terminate()
                except ProcessLookupError:
                    return
            try:
                await asyncio.wait_for(process.wait(), timeout=grace)
                return
            except asyncio.TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        return
                await process.wait()
                return

        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=grace)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                return
            await process.wait()
        except ProcessLookupError:
            return

    async def read_file(self, path: str) -> bytes:
        """Read a sandbox file as raw bytes.

        Files under ``/workspace`` and ``/tmp`` are read by a command running
        inside Bubblewrap. The in-sandbox check resolves the target and
        rejects anything outside the writable mounts, which stops a symlink
        from redirecting reads to a read-only system mount or another device.
        This is a best-effort check inside the sandbox, not a guard against a
        target swapped between the resolve and the read.
        """
        sandbox_path = self._sandbox_path_for(path)
        result = await self.exec_shell(
            [
                "sh",
                "-c",
                (
                    'resolved=$(realpath -e -- "$1") || exit 66; '
                    'case "$resolved" in '
                    "/workspace|/workspace/*|/tmp|/tmp/*) "
                    'cat -- "$resolved" ;; '
                    "*) exit 65 ;; "
                    "esac"
                ),
                "sh",
                sandbox_path,
            ],
        )
        if result.exit_code == 66:
            raise FileNotFoundError(f"not found in Bubblewrap sandbox: {path}")
        if result.exit_code == 65:
            raise PermissionError(
                f"path escapes writable Bubblewrap mounts: {path}",
            )
        if not result.ok():
            raise RuntimeError(
                "Bubblewrap read_file failed "
                f"(exit {result.exit_code}): "
                f"{result.stderr.decode(errors='replace')}",
            )
        return result.stdout

    async def write_file(self, path: str, data: bytes) -> None:
        """Write raw bytes, refusing every symbolic-link final component."""
        sandbox_path = self._sandbox_path_for(path)
        result = await self._exec_with_input(
            [
                "sh",
                "-c",
                (
                    'parent=$(dirname -- "$1") && '
                    'resolved_parent=$(realpath -m -- "$parent") || exit 65; '
                    'case "$resolved_parent" in '
                    "/workspace|/workspace/*|/tmp|/tmp/*) ;; "
                    "*) exit 65 ;; "
                    "esac; "
                    'mkdir -p -- "$resolved_parent" && '
                    'target="$resolved_parent/$(basename -- "$1")" && '
                    '[ ! -L "$target" ] || exit 65; '
                    'cat > "$target"'
                ),
                "sh",
                sandbox_path,
            ],
            data,
        )
        if result.exit_code == 65:
            raise PermissionError(
                f"path escapes writable Bubblewrap mounts: {path}",
            )
        if not result.ok():
            raise RuntimeError(
                "Bubblewrap write_file failed "
                f"(exit {result.exit_code}): "
                f"{result.stderr.decode(errors='replace')}",
            )

    async def _exec_with_input(
        self,
        command: list[str],
        data: bytes,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run a sandbox command with ``data`` piped to stdin."""
        try:
            process = await self.start_process(
                command,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            return ExecResult(
                exit_code=127,
                stdout=b"",
                stderr=str(exc).encode("utf-8"),
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(data),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await self._terminate_process_tree(process, grace=1.0)
            return ExecResult(exit_code=-1, stdout=b"", stderr=b"timed out")
        except BaseException:
            await asyncio.shield(
                self._terminate_process_tree(process, grace=1.0),
            )
            raise

        return ExecResult(
            exit_code=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )

    def _bwrap_argv(
        self,
        command: list[str],
        *,
        cwd: str | None,
    ) -> list[str]:
        """Build the ``bwrap`` argv for one sandboxed process."""
        self._validate_mount_sources()

        # ``--bind``/``--ro-bind``/``--tmpfs`` all create their destination
        # mount points (including parents), so an explicit ``--dir`` before a
        # same-path mount is redundant. ``--dir /var`` is kept because the
        # tmpfs below only covers ``/var/tmp``.
        args = [
            "bwrap",
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--bind",
            self._host_workdir,
            SANDBOX_WORKDIR,
            "--bind",
            self._host_tmpdir,
            SANDBOX_TMPDIR,
            "--tmpfs",
            "/run",
            "--dir",
            "/var",
            "--tmpfs",
            "/var/tmp",
        ]
        if self._host_cache_dir is not None:
            args.extend(
                [
                    "--bind",
                    self._host_cache_dir,
                    SANDBOX_CACHE_DIR,
                ],
            )
        if self._share_net:
            args.append("--share-net")

        args.extend(self._readonly_system_mounts())

        sandbox_cwd = cwd or self._workdir
        env = dict(self._env)
        env.update(self._base_env())
        env["PWD"] = sandbox_cwd
        args.append("--clearenv")
        for key, value in env.items():
            args.extend(["--setenv", key, str(value)])

        args.extend(["--chdir", sandbox_cwd, "--"])
        args.extend(command)
        return args

    def _validate_mount_sources(self) -> None:
        """Ensure bind sources were not removed or replaced."""
        for label, path, expected_identity in (
            (
                "host_workdir",
                self._host_workdir,
                self._host_workdir_identity,
            ),
            (
                "host_tmpdir",
                self._host_tmpdir,
                self._host_tmpdir_identity,
            ),
        ):
            try:
                identity = self._directory_identity(path, label=label)
            except ValueError as exc:
                raise RuntimeError(
                    f"{label} was removed or replaced before execution.",
                ) from exc
            if identity != expected_identity:
                raise RuntimeError(
                    f"{label} was replaced before execution.",
                )

        if self._host_cache_dir is None:
            return
        try:
            identity = self._directory_identity(
                self._host_cache_dir,
                label="host_cache_dir",
            )
        except ValueError as exc:
            raise RuntimeError(
                "host_cache_dir was removed or replaced before execution.",
            ) from exc
        if identity != self._host_cache_identity:
            raise RuntimeError(
                "host_cache_dir was replaced before execution.",
            )

    @staticmethod
    def _directory_identity(
        path: str,
        *,
        label: str,
    ) -> tuple[int, int]:
        """Return a stable identity for a real directory mount source."""
        if os.path.islink(path) or not os.path.isdir(path):
            raise ValueError(f"{label} must be a real directory: {path}")
        stat_result = os.stat(path, follow_symlinks=False)
        return stat_result.st_dev, stat_result.st_ino

    @staticmethod
    def _paths_overlap(left: str, right: str) -> bool:
        """Return whether either real path contains the other."""
        left_real = os.path.realpath(left)
        right_real = os.path.realpath(right)
        try:
            common = os.path.commonpath([left_real, right_real])
        except ValueError:
            return False
        return common in (left_real, right_real)

    def _base_env(self) -> dict[str, str]:
        """Environment visible to sandboxed commands."""
        path_parts = [
            f"{SANDBOX_WORKDIR}/.agentscope/.venv/bin",
            f"{SANDBOX_WORKDIR}/.agentscope/bin",
            "/usr/local/sbin",
            "/usr/local/bin",
            "/usr/sbin",
            "/usr/bin",
            "/sbin",
            "/bin",
        ]
        return {
            "HOME": SANDBOX_WORKDIR,
            "TMPDIR": SANDBOX_TMPDIR,
            "UV_CACHE_DIR": f"{SANDBOX_CACHE_DIR}/uv",
            "XDG_CACHE_HOME": f"{SANDBOX_CACHE_DIR}/xdg",
            "PIP_CACHE_DIR": f"{SANDBOX_CACHE_DIR}/pip",
            "PATH": ":".join(path_parts),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONUNBUFFERED": "1",
        }

    def _readonly_system_mounts(self) -> list[str]:
        """Mount host system directories read-only inside the sandbox."""
        args: list[str] = []
        for path in ("/usr",):
            if os.path.exists(path):
                args.extend(["--ro-bind", path, path])
        args.extend(self._readonly_etc_mounts())

        for path in ("/bin", "/sbin", "/lib", "/lib64"):
            if not os.path.exists(path):
                continue
            if os.path.islink(path):
                args.extend(["--symlink", os.readlink(path), path])
            else:
                args.extend(["--ro-bind", path, path])
        return args

    @staticmethod
    def _readonly_etc_mounts() -> list[str]:
        """Mount only host ``/etc`` entries needed for networking/runtime."""
        args = ["--dir", "/etc"]
        for path in (
            "/etc/resolv.conf",
            "/etc/hosts",
            "/etc/nsswitch.conf",
            "/etc/passwd",
            "/etc/group",
        ):
            if os.path.exists(path):
                args.extend(["--ro-bind", path, path])
        for path in (
            "/etc/alternatives",
            "/etc/ssl",
            "/etc/pki",
            "/etc/ca-certificates",
        ):
            if os.path.exists(path):
                args.extend(["--ro-bind", path, path])
        return args

    def _sandbox_path_for(self, path: str) -> str:
        """Validate and normalize a writable sandbox path."""
        sandbox_path = PurePosixPath(path)
        if not sandbox_path.is_absolute():
            raise ValueError(f"Sandbox path must be absolute: {path!r}")

        normalized = posixpath.normpath(path)
        for sandbox_root in (SANDBOX_WORKDIR, SANDBOX_TMPDIR):
            if normalized == sandbox_root:
                return normalized
            prefix = sandbox_root + "/"
            if normalized.startswith(prefix):
                return normalized

        raise PermissionError(
            "Bubblewrap file access is limited to "
            f"{SANDBOX_WORKDIR!r} and {SANDBOX_TMPDIR!r}: {path!r}",
        )
