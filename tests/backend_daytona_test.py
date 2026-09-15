# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`DaytonaBackend`.

A live smoke suite exercises the real SDK mapping and is skipped unless
``DAYTONA_API_KEY`` is set.
"""

import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.async_case import IsolatedAsyncioTestCase

from _daytona_live_utils import (
    DAYTONA_API_KEY,
    SKIP_REASON,
    delete_live_daytona_workspace,
    live_daytona_kwargs,
    live_daytona_workspace_id,
)

from agentscope.workspace import DaytonaBackend, DaytonaWorkspace


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
