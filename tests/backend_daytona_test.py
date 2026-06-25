# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`DaytonaBackend`.

The mock tests validate the SDK mapping without requiring Daytona
credentials. A small live smoke suite is skipped unless
``DAYTONA_API_KEY`` is set.
"""

import unittest
from types import SimpleNamespace
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


class _FakeProcess:
    """Minimal Daytona ``sandbox.process`` fake."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.response: object = SimpleNamespace(
            exit_code=0,
            result="",
            stdout=None,
            stderr=None,
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
        """Return stored bytes or raise a generic SDK-shaped error."""
        if path not in self.files:
            raise RuntimeError("file not found")
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
        self.sandbox = _FakeSandbox()
        self.backend = DaytonaBackend(
            self.sandbox,
            workdir="/home/daytona",
        )

    async def test_exec_maps_argv_to_posix_quoted_command(self) -> None:
        """``exec_shell`` passes one POSIX-quoted command string."""
        self.sandbox.process.response = SimpleNamespace(
            exit_code=0,
            result="a b | ;\n",
            stdout=None,
            stderr="",
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
            result="",
            stdout="out",
            stderr="err",
        )

        result = await self.backend.exec_shell(
            ["sh", "-c", "exit 7"],
            cwd="/tmp",
            timeout=3,
        )

        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.stdout, b"out")
        self.assertEqual(result.stderr, b"err")
        self.assertEqual(
            self.sandbox.process.calls[-1],
            ("sh -c 'exit 7'", {"cwd": "/tmp", "timeout": 3}),
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
            stdout="",
            stderr="",
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
            stdout="",
            stderr="",
        )

        self.assertFalse(
            await self.backend.file_exists("/home/daytona/missing"),
        )
        self.assertFalse(await self.backend.is_dir("/home/daytona/f.txt"))

    async def test_list_dir_uses_inherited_shell_helper(self) -> None:
        """Non-recursive ``list_dir`` parses NUL-delimited output."""
        self.sandbox.process.response = SimpleNamespace(
            exit_code=0,
            result="",
            stdout="a.txt\0b.txt\0",
            stderr="",
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
            result="",
            stdout="/home/daytona/top.txt\0/home/daytona/sub/nested.txt\0",
            stderr="",
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
            result="",
            stdout="1710000000\n",
            stderr="",
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
            stdout="",
            stderr="",
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
    """Small live smoke tests against Daytona, skipped without credentials."""

    async def test_exec_write_read_smoke(self) -> None:
        """Bash execution and byte file I/O work in one live sandbox."""
        workspace_id = live_daytona_workspace_id("daytona-live-backend")
        workspace = DaytonaWorkspace(
            workspace_id=workspace_id,
            **live_daytona_kwargs(),
        )
        try:
            await workspace.initialize()
            backend = workspace._backend
            self.assertIsInstance(backend, DaytonaBackend)

            result = await backend.exec_shell(["echo", "hello daytona"])
            self.assertTrue(result.ok())
            self.assertEqual(result.stdout.decode().strip(), "hello daytona")

            path = f"{workspace.workdir}/roundtrip.txt"
            await backend.write_file(path, b"hello\nworld\n")
            self.assertEqual(
                await backend.read_file(path),
                b"hello\nworld\n",
            )
        finally:
            await workspace.close()
            await delete_live_daytona_workspace(workspace_id)


if __name__ == "__main__":
    unittest.main()
