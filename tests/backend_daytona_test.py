# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`DaytonaBackend`.

The mock tests validate the SDK mapping without requiring Daytona
credentials. A small live smoke suite is skipped unless
``DAYTONA_API_KEY`` is set.
"""

import unittest
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase

from _daytona_live_utils import (
    DAYTONA_API_KEY,
    SKIP_REASON,
    delete_live_daytona_workspace,
    live_daytona_kwargs,
    live_daytona_workspace_id,
)

from agentscope.tool import ExecResult
from agentscope.workspace import DaytonaBackend, DaytonaWorkspace


class _FakeDaytonaNotFoundError(Exception):
    """Fake Daytona SDK not-found exception for mock backend tests."""


class _FakeProcess:
    """Minimal Daytona ``sandbox.process`` fake."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.response: object = SimpleNamespace(
            exit_code=0,
            result="",
            artifacts=SimpleNamespace(stdout=""),
            additional_properties={},
        )

    async def exec(self, command: str, **kwargs: object) -> object:
        """Record the call and return the configured response."""
        self.calls.append((command, kwargs))
        return self.response


class _FakeFS:
    """Minimal Daytona ``sandbox.fs`` fake."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.uploads: list[tuple[bytes, str]] = []

    async def download_file(self, path: str) -> bytes:
        """Return stored bytes or raise SDK not-found error."""
        if path not in self.files:
            raise _FakeDaytonaNotFoundError("file not found")
        return self.files[path]

    async def upload_file(self, data: bytes, remote_path: str) -> None:
        """Store uploaded bytes."""
        self.files[remote_path] = data
        self.uploads.append((data, remote_path))


class _FakeSandbox:
    """Minimal Daytona sandbox fake for backend tests."""

    def __init__(self) -> None:
        self.process = _FakeProcess()
        self.fs = _FakeFS()


class TestDaytonaBackendMock(IsolatedAsyncioTestCase):
    """Backend behavior that does not require a live Daytona sandbox."""

    async def asyncSetUp(self) -> None:
        """Create a fake sandbox-backed backend."""
        self._real_daytona_module = sys.modules.get("daytona")
        fake_daytona = ModuleType("daytona")
        fake_daytona.DaytonaNotFoundError = _FakeDaytonaNotFoundError
        sys.modules["daytona"] = fake_daytona

        self.sandbox = _FakeSandbox()
        self.backend = DaytonaBackend(
            self.sandbox,
            workdir="/home/daytona",
        )

    async def asyncTearDown(self) -> None:
        """Restore the optional Daytona SDK module after mock tests."""
        if self._real_daytona_module is None:
            sys.modules.pop("daytona", None)
        else:
            sys.modules["daytona"] = self._real_daytona_module

    async def test_exec_maps_argv_to_posix_quoted_command(self) -> None:
        """``exec_shell`` passes one POSIX-quoted command string."""
        self.sandbox.process.response = SimpleNamespace(
            exit_code=0,
            result="a b | ;\n",
            artifacts=SimpleNamespace(stdout="a b | ;\n"),
            additional_properties={},
        )

        result = await self.backend.exec_shell(["echo", "a b | ;"])

        self.assertIsInstance(result, ExecResult)
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout, b"a b | ;\n")
        self.assertEqual(
            self.sandbox.process.calls,
            [("echo 'a b | ;'", {"cwd": "/home/daytona"})],
        )

    async def test_exec_forwards_cwd_and_timeout(self) -> None:
        """Explicit ``cwd`` and ``timeout`` are forwarded to the SDK."""
        self.sandbox.process.response = SimpleNamespace(
            exit_code=7,
            result="merged output",
            artifacts=SimpleNamespace(stdout="merged output"),
            additional_properties={},
        )

        result = await self.backend.exec_shell(
            ["sh", "-c", "exit 7"],
            cwd="/tmp",
            timeout=3.2,
        )

        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.stdout, b"merged output")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            self.sandbox.process.calls[-1],
            ("sh -c 'exit 7'", {"cwd": "/tmp", "timeout": 4}),
        )

    async def test_exec_transport_error_returns_minus_one(self) -> None:
        """SDK transport failures become an ``ExecResult`` sentinel."""

        async def _boom(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("network down")

        self.sandbox.process.exec = _boom

        result = await self.backend.exec_shell(["echo", "x"])

        self.assertEqual(result.exit_code, -1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"network down", result.stderr)

    async def test_read_write_roundtrip_uses_daytona_fs(self) -> None:
        """``read_file`` / ``write_file`` use the Daytona FS API."""
        await self.backend.write_file("/home/daytona/a/b.bin", b"a\x00b")

        self.assertEqual(
            self.sandbox.process.calls[0][0],
            "mkdir -p /home/daytona/a",
        )
        self.assertEqual(
            await self.backend.read_file("/home/daytona/a/b.bin"),
            b"a\x00b",
        )
        self.assertEqual(
            self.sandbox.fs.uploads,
            [(b"a\x00b", "/home/daytona/a/b.bin")],
        )

    async def test_read_missing_file_maps_to_file_not_found(self) -> None:
        """Missing sandbox files raise the standard ``FileNotFoundError``."""
        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file("/home/daytona/nope.txt")

    async def test_file_exists_and_is_dir_use_inherited_shell_helpers(
        self,
    ) -> None:
        """``file_exists`` / ``is_dir`` dispatch through Daytona exec."""
        self.sandbox.process.response = SimpleNamespace(
            exit_code=0,
            result="",
            artifacts=SimpleNamespace(stdout=""),
            additional_properties={},
        )

        self.assertTrue(await self.backend.file_exists("/home/daytona/f.txt"))
        self.assertTrue(await self.backend.is_dir("/home/daytona"))

        self.assertEqual(
            self.sandbox.process.calls[-2:],
            [
                ("test -e /home/daytona/f.txt", {"cwd": "/home/daytona"}),
                ("test -d /home/daytona", {"cwd": "/home/daytona"}),
            ],
        )

        self.sandbox.process.response = SimpleNamespace(
            exit_code=1,
            result="",
            artifacts=SimpleNamespace(stdout=""),
            additional_properties={},
        )

        self.assertFalse(
            await self.backend.file_exists("/home/daytona/missing"),
        )
        self.assertFalse(await self.backend.is_dir("/home/daytona/f.txt"))

    async def test_list_dir_uses_inherited_shell_helper(self) -> None:
        """Non-recursive ``list_dir`` parses NUL-delimited output."""
        self.sandbox.process.response = SimpleNamespace(
            exit_code=0,
            result="a.txt\0b.txt\0",
            artifacts=SimpleNamespace(stdout="a.txt\0b.txt\0"),
            additional_properties={},
        )

        entries = await self.backend.list_dir("/home/daytona/listing")

        self.assertEqual(entries, ["a.txt", "b.txt"])
        self.assertEqual(
            self.sandbox.process.calls[-1],
            (
                "find /home/daytona/listing -mindepth 1 -maxdepth 1 "
                "-printf '%f\\0'",
                {"cwd": "/home/daytona"},
            ),
        )

    async def test_list_dir_recursive_uses_inherited_shell_helper(
        self,
    ) -> None:
        """Recursive ``list_dir`` returns sandbox file paths."""
        self.sandbox.process.response = SimpleNamespace(
            exit_code=0,
            result="/home/daytona/top.txt\0/home/daytona/sub/nested.txt\0",
            artifacts=SimpleNamespace(
                stdout="/home/daytona/top.txt\0"
                "/home/daytona/sub/nested.txt\0",
            ),
            additional_properties={},
        )

        entries = await self.backend.list_dir(
            "/home/daytona/rec",
            recursive=True,
        )

        self.assertEqual(
            entries,
            ["/home/daytona/top.txt", "/home/daytona/sub/nested.txt"],
        )
        self.assertEqual(
            self.sandbox.process.calls[-1],
            (
                "find /home/daytona/rec -type f -print0",
                {"cwd": "/home/daytona"},
            ),
        )

    async def test_stat_mtime_uses_inherited_shell_helper(self) -> None:
        """``stat_mtime`` returns float mtime from sandbox shell output."""
        self.sandbox.process.response = SimpleNamespace(
            exit_code=0,
            result="1710000000\n",
            artifacts=SimpleNamespace(stdout="1710000000\n"),
            additional_properties={},
        )

        mtime = await self.backend.stat_mtime("/home/daytona/stat.txt")

        self.assertEqual(mtime, 1710000000.0)
        command, kwargs = self.sandbox.process.calls[-1]
        self.assertEqual(kwargs, {"cwd": "/home/daytona"})
        self.assertIn("stat -c %Y /home/daytona/stat.txt", command)
        self.assertIn("stat -f %m /home/daytona/stat.txt", command)

        self.sandbox.process.response = SimpleNamespace(
            exit_code=1,
            result="",
            artifacts=SimpleNamespace(stdout=""),
            additional_properties={},
        )
        self.assertIsNone(
            await self.backend.stat_mtime("/home/daytona/missing"),
        )

    async def test_delete_path_uses_inherited_shell_helper(self) -> None:
        """``delete_path`` dispatches ``rm -rf`` through Daytona exec."""
        await self.backend.delete_path("/home/daytona/to-delete")

        self.assertEqual(
            self.sandbox.process.calls[-1],
            ("rm -rf /home/daytona/to-delete", {"cwd": "/home/daytona"}),
        )


@unittest.skipUnless(DAYTONA_API_KEY, SKIP_REASON)
class TestDaytonaBackendLive(IsolatedAsyncioTestCase):
    """Live Daytona backend parity, skipped without credentials."""

    @asynccontextmanager
    async def _live_backend(
        self,
        suffix: str,
    ) -> AsyncIterator[tuple[DaytonaWorkspace, DaytonaBackend]]:
        """Create one live workspace and yield its backend."""
        workspace_id = live_daytona_workspace_id(suffix)
        workspace = DaytonaWorkspace(
            workspace_id=workspace_id,
            **live_daytona_kwargs(),
        )
        try:
            await workspace.initialize()
            backend = workspace._backend
            self.assertIsInstance(backend, DaytonaBackend)
            yield workspace, backend
        finally:
            await workspace.close()
            await delete_live_daytona_workspace(workspace_id)

    # ── exec ───────────────────────────────────────────────────────

    async def test_exec_returns_stdout(self) -> None:
        """A program's stdout/exit code are captured into ``ExecResult``."""
        async with self._live_backend("daytona-live-exec-stdout") as (
            _workspace,
            backend,
        ):
            result = await backend.exec_shell(["echo", "hello daytona"])

        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().strip(), "hello daytona")
        self.assertEqual(result.stderr, b"")

    async def test_exec_nonzero_exit(self) -> None:
        """A non-zero command exit is reported as a normal result."""
        async with self._live_backend("daytona-live-exec-nonzero") as (
            _workspace,
            backend,
        ):
            result = await backend.exec_shell(
                ["sh", "-c", "printf daytona-oops; exit 4"],
            )

        self.assertEqual(result.exit_code, 4)
        self.assertIn("daytona-oops", result.stdout.decode())
        self.assertEqual(result.stderr, b"")

    async def test_exec_argv_quoting_preserved(self) -> None:
        """An argv element with metacharacters survives SDK command mapping."""
        tricky = "a b $(echo x) | ;"
        async with self._live_backend("daytona-live-exec-argv") as (
            _workspace,
            backend,
        ):
            result = await backend.exec_shell(["echo", tricky])

        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().rstrip("\n"), tricky)

    async def test_exec_cwd_default_is_workdir(self) -> None:
        """With no explicit ``cwd`` the sandbox workdir is used."""
        async with self._live_backend("daytona-live-exec-cwd") as (
            workspace,
            backend,
        ):
            result = await backend.exec_shell(["pwd"])

        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().strip(), workspace.workdir)

    # ── file I/O ───────────────────────────────────────────────────

    async def test_write_then_read_roundtrip(self) -> None:
        """Bytes written into the sandbox are read back verbatim."""
        async with self._live_backend("daytona-live-file-roundtrip") as (
            workspace,
            backend,
        ):
            path = f"{workspace.workdir}/roundtrip.txt"
            payload = b"hello\nworld\n"
            await backend.write_file(path, payload)
            self.assertEqual(await backend.read_file(path), payload)

    async def test_write_creates_parent_dirs(self) -> None:
        """``write_file`` creates missing parent directories."""
        async with self._live_backend("daytona-live-file-parent") as (
            workspace,
            backend,
        ):
            path = f"{workspace.workdir}/a/b/c/file.txt"
            await backend.write_file(path, b"x")
            self.assertEqual(await backend.read_file(path), b"x")

    async def test_write_preserves_binary(self) -> None:
        """Raw bytes survive the Daytona file upload/download round-trip."""
        async with self._live_backend("daytona-live-file-binary") as (
            workspace,
            backend,
        ):
            path = f"{workspace.workdir}/bin.dat"
            payload = b"a\r\nb\x00\xffc"
            await backend.write_file(path, payload)
            self.assertEqual(await backend.read_file(path), payload)

    async def test_read_missing_file_raises(self) -> None:
        """Reading a non-existent file raises ``FileNotFoundError``."""
        async with self._live_backend("daytona-live-file-missing") as (
            workspace,
            backend,
        ):
            with self.assertRaises(FileNotFoundError):
                await backend.read_file(f"{workspace.workdir}/nope.txt")

    # ── derived filesystem helpers (shell-based) ───────────────────

    async def test_file_exists_and_is_dir(self) -> None:
        """``file_exists`` / ``is_dir`` reflect the sandbox filesystem."""
        async with self._live_backend("daytona-live-helper-exists") as (
            workspace,
            backend,
        ):
            path = f"{workspace.workdir}/f.txt"
            await backend.write_file(path, b"x")
            self.assertTrue(await backend.file_exists(path))
            self.assertTrue(await backend.file_exists(workspace.workdir))
            self.assertTrue(await backend.is_dir(workspace.workdir))
            self.assertFalse(await backend.is_dir(path))
            self.assertFalse(
                await backend.file_exists(f"{workspace.workdir}/missing"),
            )

    async def test_list_dir(self) -> None:
        """Non-recursive ``list_dir`` returns immediate child base names."""
        async with self._live_backend("daytona-live-helper-list") as (
            workspace,
            backend,
        ):
            base = f"{workspace.workdir}/listing"
            await backend.write_file(f"{base}/a.txt", b"x")
            await backend.write_file(f"{base}/b.txt", b"x")
            entries = await backend.list_dir(base)

        self.assertEqual(sorted(entries), ["a.txt", "b.txt"])

    async def test_list_dir_recursive(self) -> None:
        """Recursive ``list_dir`` returns file paths underneath the root."""
        async with self._live_backend("daytona-live-helper-recursive") as (
            workspace,
            backend,
        ):
            base = f"{workspace.workdir}/rec"
            await backend.write_file(f"{base}/top.txt", b"x")
            await backend.write_file(f"{base}/sub/nested.txt", b"x")
            entries = await backend.list_dir(base, recursive=True)

        basenames = sorted(e.rsplit("/", 1)[-1] for e in entries)
        self.assertEqual(basenames, ["nested.txt", "top.txt"])

    async def test_stat_mtime(self) -> None:
        """``stat_mtime`` returns a float for an existing path, None else."""
        async with self._live_backend("daytona-live-helper-stat") as (
            workspace,
            backend,
        ):
            path = f"{workspace.workdir}/stat.txt"
            await backend.write_file(path, b"x")
            mtime = await backend.stat_mtime(path)
            missing = await backend.stat_mtime(f"{workspace.workdir}/missing")

        self.assertIsInstance(mtime, float)
        self.assertIsNone(missing)

    async def test_delete_path(self) -> None:
        """``delete_path`` removes files and trees; missing is a no-op."""
        async with self._live_backend("daytona-live-helper-delete") as (
            workspace,
            backend,
        ):
            path = f"{workspace.workdir}/to_delete.txt"
            await backend.write_file(path, b"x")
            await backend.delete_path(path)
            self.assertFalse(await backend.file_exists(path))

            tree = f"{workspace.workdir}/tree"
            await backend.write_file(f"{tree}/deep/f.txt", b"x")
            await backend.delete_path(tree)
            self.assertFalse(await backend.file_exists(tree))

            await backend.delete_path(f"{workspace.workdir}/missing")


if __name__ == "__main__":
    unittest.main()
