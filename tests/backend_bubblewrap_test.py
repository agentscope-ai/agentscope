# -*- coding: utf-8 -*-
# pylint: disable=protected-access,consider-using-with,too-many-public-methods
"""Test cases for :class:`BubblewrapBackend`."""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import asyncio
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.tool import ExecResult
from agentscope.workspace import BubblewrapBackend
from agentscope.workspace._bubblewrap._constants import (
    BWRAP_SMOKE_PROBE_ARGV,
    SANDBOX_CACHE_DIR,
    SANDBOX_WORKDIR,
)


def _bubblewrap_available() -> bool:
    """Return ``True`` iff ``bwrap`` can run a trivial command."""
    if not sys.platform.startswith("linux"):
        return False
    if shutil.which("bwrap") is None:
        return False
    try:
        result = subprocess.run(
            BWRAP_SMOKE_PROBE_ARGV,
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


_BWRAP_OK = _bubblewrap_available()
_SKIP_REASON = "Bubblewrap backend requires Linux with a working bwrap"


class TestBubblewrapBackendUnit(IsolatedAsyncioTestCase):
    """Unit tests that do not invoke the real ``bwrap`` executable."""

    async def asyncSetUp(self) -> None:
        """Build a backend with host dirs only."""
        # pylint: disable=consider-using-with
        self.workdir = tempfile.TemporaryDirectory()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.backend = BubblewrapBackend(
            host_workdir=self.workdir.name,
            host_tmpdir=self.tmpdir.name,
        )

    async def asyncTearDown(self) -> None:
        """Clean up host dirs."""
        self.workdir.cleanup()
        self.tmpdir.cleanup()

    async def test_bwrap_argv_uses_unshare_all_and_share_net(self) -> None:
        """Default command isolates namespaces and restores networking."""
        argv = self.backend._bwrap_argv(["true"], cwd=None)
        self.assertIn("--unshare-all", argv)
        self.assertIn("--share-net", argv)
        self.assertIn("--new-session", argv)
        self.assertIn("--die-with-parent", argv)
        self.assertIn("--clearenv", argv)

    async def test_bwrap_argv_does_not_mount_opt_by_default(self) -> None:
        """Host /opt is not exposed unless a future explicit mount adds it."""
        argv = self.backend._bwrap_argv(["true"], cwd=None)
        ro_bind_targets = [
            argv[i + 2]
            for i, item in enumerate(argv)
            if item == "--ro-bind" and i + 2 < len(argv)
        ]
        self.assertNotIn("/opt", ro_bind_targets)

    async def test_bwrap_argv_can_unshare_network(self) -> None:
        """``share_net=False`` leaves networking isolated."""
        backend = BubblewrapBackend(
            host_workdir=self.workdir.name,
            host_tmpdir=self.tmpdir.name,
            share_net=False,
        )
        argv = backend._bwrap_argv(["true"], cwd=None)
        self.assertIn("--unshare-all", argv)
        self.assertNotIn("--share-net", argv)

    async def test_bwrap_argv_can_mount_shared_cache(self) -> None:
        """An optional host cache is writable at the sandbox cache path."""
        cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cache_dir.cleanup)
        backend = BubblewrapBackend(
            host_workdir=self.workdir.name,
            host_tmpdir=self.tmpdir.name,
            host_cache_dir=cache_dir.name,
        )

        argv = backend._bwrap_argv(["true"], cwd=None)

        self.assertIn(SANDBOX_CACHE_DIR, argv)
        bind_index = argv.index(os.path.realpath(cache_dir.name))
        self.assertEqual(argv[bind_index - 1], "--bind")
        self.assertEqual(argv[bind_index + 1], SANDBOX_CACHE_DIR)

    async def test_replaced_cache_bind_source_is_rejected(self) -> None:
        """A cache root replacement is detected before bwrap starts."""
        with tempfile.TemporaryDirectory() as parent:
            cache_dir = os.path.join(parent, "cache")
            outside_dir = os.path.join(parent, "outside")
            os.makedirs(cache_dir)
            os.makedirs(outside_dir)
            backend = BubblewrapBackend(
                host_workdir=self.workdir.name,
                host_tmpdir=self.tmpdir.name,
                host_cache_dir=cache_dir,
            )

            os.rmdir(cache_dir)
            try:
                os.symlink(outside_dir, cache_dir)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink unavailable: {exc}")

            with self.assertRaisesRegex(RuntimeError, "removed or replaced"):
                backend._bwrap_argv(["true"], cwd=None)

    async def test_cache_bind_source_cannot_overlap_workdir(self) -> None:
        """Direct backend construction enforces the cache boundary."""
        for cache_dir in (
            os.path.join(self.workdir.name, "cache"),
            os.path.dirname(self.workdir.name),
        ):
            with self.assertRaisesRegex(ValueError, "must not overlap"):
                BubblewrapBackend(
                    host_workdir=self.workdir.name,
                    host_tmpdir=self.tmpdir.name,
                    host_cache_dir=cache_dir,
                )

    async def test_workdir_cannot_overlap_tmpdir(self) -> None:
        """Workspace and tmp bind sources must remain separate."""
        with tempfile.TemporaryDirectory() as parent:
            workdir = os.path.join(parent, "workspace")
            tmpdir = os.path.join(workdir, "tmp")
            with self.assertRaisesRegex(ValueError, "must not overlap"):
                BubblewrapBackend(
                    host_workdir=workdir,
                    host_tmpdir=tmpdir,
                )

    async def test_cache_cannot_overlap_tmpdir(self) -> None:
        """Cache and tmp bind sources must remain separate."""
        with tempfile.TemporaryDirectory() as parent:
            workdir = os.path.join(parent, "workspace")
            tmpdir = os.path.join(parent, "tmp")
            cache_dir = os.path.join(tmpdir, "cache")
            with self.assertRaisesRegex(ValueError, "must not overlap"):
                BubblewrapBackend(
                    host_workdir=workdir,
                    host_tmpdir=tmpdir,
                    host_cache_dir=cache_dir,
                )

    @unittest.skipIf(os.name == "nt", "POSIX mode bits only")
    async def test_new_host_mounts_use_private_permissions(self) -> None:
        """New backend-owned mount directories use mode 0700."""
        with tempfile.TemporaryDirectory() as parent:
            workdir = os.path.join(parent, "workspace")
            tmpdir = os.path.join(parent, "tmp")
            cache_dir = os.path.join(parent, "cache")

            BubblewrapBackend(
                host_workdir=workdir,
                host_tmpdir=tmpdir,
                host_cache_dir=cache_dir,
            )

            self.assertEqual(
                stat.S_IMODE(os.stat(workdir).st_mode),
                0o700,
            )
            self.assertEqual(
                stat.S_IMODE(os.stat(tmpdir).st_mode),
                0o700,
            )
            self.assertEqual(
                stat.S_IMODE(os.stat(cache_dir).st_mode),
                0o700,
            )

    async def test_rejects_empty_host_paths(self) -> None:
        """Host mount roots must be explicit non-empty paths."""
        with self.assertRaises(ValueError):
            BubblewrapBackend(host_workdir="", host_tmpdir=self.tmpdir.name)
        with self.assertRaises(ValueError):
            BubblewrapBackend(host_workdir=self.workdir.name, host_tmpdir="")
        with self.assertRaises(ValueError):
            BubblewrapBackend(host_workdir="   ", host_tmpdir=self.tmpdir.name)
        with self.assertRaises(ValueError):
            BubblewrapBackend(
                host_workdir=self.workdir.name,
                host_tmpdir="   ",
            )
        with self.assertRaises(ValueError):
            BubblewrapBackend(
                host_workdir=self.workdir.name,
                host_tmpdir=self.tmpdir.name,
                host_cache_dir="   ",
            )

    async def test_native_io_rejects_relative_paths(self) -> None:
        """Native file access requires absolute sandbox paths."""
        with self.assertRaises(ValueError):
            await self.backend.write_file("relative.txt", b"x")

    async def test_native_io_rejects_unmounted_paths(self) -> None:
        """Native file access is limited to ``/workspace`` and ``/tmp``."""
        with self.assertRaises(PermissionError):
            await self.backend.write_file("/etc/hosts", b"x")
        with self.assertRaises(PermissionError):
            await self.backend.read_file("/etc/hosts")

    @unittest.skipUnless(_BWRAP_OK, _SKIP_REASON)
    async def test_native_io_rejects_parent_symlink_escape(self) -> None:
        """A symlinked parent cannot redirect writes outside the sandbox."""
        try:
            os.symlink(
                "/unmounted-dir",
                os.path.join(self.workdir.name, "out"),
            )
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        with self.assertRaises(PermissionError):
            await self.backend.write_file(
                f"{SANDBOX_WORKDIR}/out/file.txt",
                b"x",
            )

    @unittest.skipUnless(_BWRAP_OK, _SKIP_REASON)
    async def test_native_io_rejects_file_symlink_escape(self) -> None:
        """A symlinked file cannot redirect reads outside the sandbox."""
        try:
            os.symlink(
                "/unmounted-secret",
                os.path.join(self.workdir.name, "secret"),
            )
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file(f"{SANDBOX_WORKDIR}/secret")

    @unittest.skipUnless(_BWRAP_OK, _SKIP_REASON)
    async def test_native_io_rejects_dangling_file_symlink_write(
        self,
    ) -> None:
        """A dangling symlink cannot redirect writes outside the sandbox."""
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        outside_file = os.path.join(outside.name, "created.txt")
        try:
            os.symlink(
                outside_file,
                os.path.join(self.workdir.name, "escape"),
            )
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        with self.assertRaises(PermissionError):
            await self.backend.write_file(
                f"{SANDBOX_WORKDIR}/escape",
                b"escaped",
            )
        self.assertFalse(os.path.exists(outside_file))

    @unittest.skipUnless(_BWRAP_OK, _SKIP_REASON)
    async def test_native_io_rejects_symlinks_to_mounted_paths(self) -> None:
        """File helpers cannot redirect through a writable mount boundary."""
        try:
            os.symlink(
                "/etc/hosts",
                os.path.join(self.workdir.name, "system-file"),
            )
            os.symlink(
                "/dev/null",
                os.path.join(self.workdir.name, "device-file"),
            )
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        with self.assertRaises(PermissionError):
            await self.backend.read_file(
                f"{SANDBOX_WORKDIR}/system-file",
            )
        with self.assertRaises(PermissionError):
            await self.backend.write_file(
                f"{SANDBOX_WORKDIR}/device-file",
                b"must not reach /dev/null",
            )

        await self.backend.write_file(
            f"{SANDBOX_WORKDIR}/real-file",
            b"internal target",
        )
        try:
            os.symlink(
                "real-file",
                os.path.join(self.workdir.name, "internal-link"),
            )
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        self.assertEqual(
            await self.backend.read_file(
                f"{SANDBOX_WORKDIR}/internal-link",
            ),
            b"internal target",
        )
        with self.assertRaises(PermissionError):
            await self.backend.write_file(
                f"{SANDBOX_WORKDIR}/internal-link",
                b"must be rejected",
            )

    async def test_base_env_cannot_be_overridden(self) -> None:
        """Critical runtime env vars are restored after user env."""
        backend = BubblewrapBackend(
            host_workdir=self.workdir.name,
            host_tmpdir=self.tmpdir.name,
            env={
                "PATH": "/custom",
                "HOME": "/custom-home",
                "TMPDIR": "/custom-tmp",
                "API_KEY": "allowed",
            },
        )
        argv = backend._bwrap_argv(["true"], cwd=None)
        env_pairs = {
            argv[i + 1]: argv[i + 2]
            for i, item in enumerate(argv)
            if item == "--setenv"
        }
        self.assertEqual(env_pairs["HOME"], SANDBOX_WORKDIR)
        self.assertEqual(env_pairs["TMPDIR"], "/tmp")
        self.assertEqual(env_pairs["UV_CACHE_DIR"], f"{SANDBOX_CACHE_DIR}/uv")
        self.assertEqual(env_pairs["PWD"], SANDBOX_WORKDIR)
        self.assertIn(
            f"{SANDBOX_WORKDIR}/.agentscope/.venv/bin",
            env_pairs["PATH"],
        )
        self.assertEqual(env_pairs["API_KEY"], "allowed")

    async def test_pwd_matches_explicit_cwd(self) -> None:
        """PWD follows the sandbox cwd instead of leaking host PWD."""
        argv = self.backend._bwrap_argv(["true"], cwd="/tmp")
        env_pairs = {
            argv[i + 1]: argv[i + 2]
            for i, item in enumerate(argv)
            if item == "--setenv"
        }
        self.assertEqual(env_pairs["PWD"], "/tmp")

    async def test_exec_shell_cancellation_terminates_process_tree(
        self,
    ) -> None:
        """Cancelling ``exec_shell`` still triggers process cleanup."""
        fake_process = AsyncMock()
        fake_process.communicate.side_effect = asyncio.CancelledError()
        fake_process.returncode = None

        with (
            patch.object(
                self.backend,
                "start_process",
                new=AsyncMock(return_value=fake_process),
            ),
            patch.object(
                BubblewrapBackend,
                "_terminate_process_tree",
                new=AsyncMock(),
            ) as terminate,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await self.backend.exec_shell(["sleep", "10"])

        terminate.assert_awaited_once_with(fake_process, grace=1.0)

    async def test_start_process_uses_new_process_group_on_posix(self) -> None:
        """POSIX subprocesses are started in their own process group."""
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=object()),
        ) as create_process:
            await self.backend.start_process(["true"])

        kwargs = create_process.call_args.kwargs
        if os.name == "nt":
            self.assertNotIn("start_new_session", kwargs)
        else:
            self.assertTrue(kwargs["start_new_session"])


@unittest.skipUnless(_BWRAP_OK, _SKIP_REASON)
class TestBubblewrapBackend(IsolatedAsyncioTestCase):
    """Backend primitive tests against live Bubblewrap."""

    async def asyncSetUp(self) -> None:
        """Build a fresh backend and mounted host dirs."""
        # pylint: disable=consider-using-with
        self.workdir = tempfile.TemporaryDirectory()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.backend = BubblewrapBackend(
            host_workdir=self.workdir.name,
            host_tmpdir=self.tmpdir.name,
        )

    async def asyncTearDown(self) -> None:
        """Drop mounted host dirs."""
        self.workdir.cleanup()
        self.tmpdir.cleanup()

    async def test_exec_returns_stdout(self) -> None:
        """A program's stdout and exit code are captured."""
        result = await self.backend.exec_shell(["echo", "hello world"])
        self.assertIsInstance(result, ExecResult)
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().strip(), "hello world")

    async def test_exec_nonzero_exit(self) -> None:
        """Non-zero exits are returned as normal results."""
        result = await self.backend.exec_shell(
            ["sh", "-c", "echo oops >&2; exit 4"],
        )
        self.assertEqual(result.exit_code, 4)
        self.assertIn("oops", result.stderr.decode())

    async def test_exec_argv_not_shell_split(self) -> None:
        """Metacharacters in one argv element are passed intact."""
        tricky = "a b $(echo x) | ;"
        result = await self.backend.exec_shell(["echo", tricky])
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().rstrip("\n"), tricky)

    async def test_exec_cwd_default_is_workdir(self) -> None:
        """With no explicit cwd, commands run in ``/workspace``."""
        result = await self.backend.exec_shell(["pwd"])
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().strip(), SANDBOX_WORKDIR)

    async def test_exec_timeout_returns_minus_one(self) -> None:
        """Timeouts use the standard -1 sentinel."""
        result = await self.backend.exec_shell(["sleep", "10"], timeout=0.2)
        self.assertEqual(result.exit_code, -1)
        self.assertEqual(result.stderr, b"timed out")

    async def test_write_then_read_roundtrip(self) -> None:
        """Bytes written under ``/workspace`` are read back verbatim."""
        path = f"{SANDBOX_WORKDIR}/roundtrip.txt"
        payload = b"hello\nworld\n"
        await self.backend.write_file(path, payload)
        self.assertEqual(await self.backend.read_file(path), payload)

    async def test_write_creates_parent_dirs(self) -> None:
        """``write_file`` creates missing parents."""
        path = f"{SANDBOX_WORKDIR}/a/b/c/file.txt"
        await self.backend.write_file(path, b"x")
        self.assertEqual(await self.backend.read_file(path), b"x")

    async def test_write_preserves_binary(self) -> None:
        """Raw bytes survive the mounted host roundtrip."""
        path = f"{SANDBOX_WORKDIR}/bin.dat"
        payload = b"a\r\nb\x00\xffc"
        await self.backend.write_file(path, payload)
        self.assertEqual(await self.backend.read_file(path), payload)

    async def test_read_missing_file_raises(self) -> None:
        """Reading a missing file raises ``FileNotFoundError``."""
        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file(f"{SANDBOX_WORKDIR}/missing.txt")

    async def test_file_exists_and_is_dir(self) -> None:
        """Inherited existence helpers reflect the sandbox filesystem."""
        path = f"{SANDBOX_WORKDIR}/f.txt"
        await self.backend.write_file(path, b"x")
        self.assertTrue(await self.backend.file_exists(path))
        self.assertTrue(await self.backend.file_exists(SANDBOX_WORKDIR))
        self.assertTrue(await self.backend.is_dir(SANDBOX_WORKDIR))
        self.assertFalse(await self.backend.is_dir(path))

    async def test_list_dir(self) -> None:
        """Non-recursive list returns immediate child base names."""
        base = f"{SANDBOX_WORKDIR}/listing"
        await self.backend.write_file(f"{base}/a.txt", b"x")
        await self.backend.write_file(f"{base}/b.txt", b"x")
        entries = await self.backend.list_dir(base)
        self.assertEqual(sorted(entries), ["a.txt", "b.txt"])

    async def test_list_dir_recursive(self) -> None:
        """Recursive list returns file paths under the root."""
        base = f"{SANDBOX_WORKDIR}/rec"
        await self.backend.write_file(f"{base}/top.txt", b"x")
        await self.backend.write_file(f"{base}/sub/nested.txt", b"x")
        entries = await self.backend.list_dir(base, recursive=True)
        basenames = sorted(e.rsplit("/", 1)[-1] for e in entries)
        self.assertEqual(basenames, ["nested.txt", "top.txt"])

    async def test_stat_mtime(self) -> None:
        """``stat_mtime`` returns a float for existing files."""
        path = f"{SANDBOX_WORKDIR}/stat.txt"
        await self.backend.write_file(path, b"x")
        self.assertIsInstance(await self.backend.stat_mtime(path), float)
        self.assertIsNone(
            await self.backend.stat_mtime(f"{SANDBOX_WORKDIR}/missing"),
        )

    async def test_delete_path(self) -> None:
        """``delete_path`` removes files and trees."""
        path = f"{SANDBOX_WORKDIR}/to_delete.txt"
        await self.backend.write_file(path, b"x")
        await self.backend.delete_path(path)
        self.assertFalse(await self.backend.file_exists(path))

        tree = f"{SANDBOX_WORKDIR}/tree"
        await self.backend.write_file(f"{tree}/deep/f.txt", b"x")
        await self.backend.delete_path(tree)
        self.assertFalse(await self.backend.file_exists(tree))

        await self.backend.delete_path(f"{SANDBOX_WORKDIR}/missing")

    async def test_cannot_read_unmounted_host_file(self) -> None:
        """Absolute host paths outside mounts are not visible in sandbox."""
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        secret = os.path.join(outside.name, "secret.txt")
        with open(secret, "wb") as f:
            f.write(b"secret")

        result = await self.backend.exec_shell(["cat", secret])
        self.assertFalse(result.ok())

    async def test_system_directory_is_read_only(self) -> None:
        """The /usr mount is explicitly read-only."""
        result = await self.backend.exec_shell(
            [
                "sh",
                "-c",
                (
                    "while IFS= read -r line; do "
                    "set -- $line; "
                    'if [ "$5" = "/usr" ]; then '
                    'case ",$6," in '
                    "*,ro,*) exit 0 ;; "
                    "*) exit 1 ;; "
                    "esac; "
                    "fi; "
                    "done < /proc/self/mountinfo; "
                    "exit 1"
                ),
            ],
        )
        self.assertTrue(
            result.ok(),
            result.stderr.decode(errors="replace"),
        )
