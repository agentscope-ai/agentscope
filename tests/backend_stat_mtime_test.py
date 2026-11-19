# -*- coding: utf-8 -*-
"""Exercise shell-backed mtimes and the file tools that trust them."""

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agentscope.state import AgentState
from agentscope.tool import Edit, Read, Write
from agentscope.tool._builtin._backend import (
    BackendBase,
    ExecResult,
    LocalBackend,
)


class _ShellBackend(LocalBackend):
    """Run the sandbox mtime helper against local files."""

    stat_mtime = BackendBase.stat_mtime

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run the actual stat command, including its platform fallback."""
        if cwd is None:
            cwd = await self.getcwd()
        return await super().exec_shell(command, cwd=cwd, timeout=timeout)


class ShellMtimeOutputTest(unittest.IsolatedAsyncioTestCase):
    """Cover both stat dialects even when only one is installed."""

    async def test_gnu_and_bsd_timestamps(self) -> None:
        """GNU/BusyBox/uutils use %y; BSD exposes seconds plus nanos."""
        cases = (
            (
                b"gnu 2026-09-21 14:13:20.123456789 +0000\n",
                1790000000.123456789,
            ),
            (
                b"gnu 2026-09-21 19:43:20.123456789 +0530\n",
                1790000000.123456789,
            ),
            # BusyBox 1.31 prints whole seconds without a timezone.
            (b"gnu 2026-09-21 14:13:20.000000000\n", 1790000000.0),
            (b"bsd 1790000000.123456789 Regular File\n", 1790000000.123456789),
            (b"gnu 1969-12-31 23:59:59.900000000 +0000\n", -0.1),
            (b"bsd -1.900000000 Regular File\n", -0.1),
            (b"gnu 1969-12-31 23:59:58.900000000 +0000\n", -1.1),
            (b"bsd -2.900000000 Regular File\n", -1.1),
            (b"gnu 1969-12-31 23:59:58.100000000 +0000\n", -1.9),
            (b"gnu 1969-12-31 23:59:59.000000000 +0000\n", -1.0),
            (b"gnu 1970-01-01 00:00:00.000000000 +0000\n", 0.0),
            (b"bsd 0.000000000 Directory\n", 0.0),
        )
        backend = _ShellBackend()
        for output, expected in cases:
            with self.subTest(output=output):
                command = AsyncMock(return_value=ExecResult(0, output, b""))
                with patch.object(backend, "exec_shell", command):
                    actual = await backend.stat_mtime("/workspace/file.txt")
                self.assertIsNotNone(actual)
                self.assertAlmostEqual(actual, expected, places=9)
                command.assert_awaited_once()

    async def test_unusable_stat_results(self) -> None:
        """Failed stats, malformed output and BSD broken links are unknown."""
        cases = (
            ExecResult(
                1,
                b"gnu 1970-01-01 00:00:00.000000000 +0000\n",
                b"denied",
            ),
            ExecResult(0, b"", b""),
            ExecResult(0, b"unknown 1790000000.123456789\n", b""),
            ExecResult(0, b"gnu not-a-timestamp\n", b""),
            ExecResult(0, b"gnu 1970-01-01 00:00:00 +0000\n", b""),
            ExecResult(0, b"gnu 1970-01-01 00:00:00.bad +0000\n", b""),
            ExecResult(0, b"gnu 1970-02-30 00:00:00.000000000 +0000\n", b""),
            ExecResult(0, b"bsd bad Regular File\n", b""),
            ExecResult(0, b"bsd 1.bad Regular File\n", b""),
            ExecResult(0, b"bsd 1.000000000\n", b""),
            ExecResult(0, b"bsd 1.000000000 Symbolic Link\n", b""),
        )
        backend = _ShellBackend()
        for result in cases:
            with self.subTest(result=result):
                with patch.object(
                    backend,
                    "exec_shell",
                    AsyncMock(return_value=result),
                ):
                    self.assertIsNone(await backend.stat_mtime("missing"))


@unittest.skipIf(
    os.name == "nt" or shutil.which("stat") is None,
    "Requires a POSIX shell and GNU or BSD stat",
)
class ShellMtimeFilesystemTest(unittest.IsolatedAsyncioTestCase):
    """Use actual stat, files and agent state, without timing sleeps."""

    async def asyncSetUp(self) -> None:
        """Give each case a private filesystem and shell working directory."""
        # pylint: disable=consider-using-with
        directory = tempfile.TemporaryDirectory(prefix="agentscope-mtime-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "file.txt"
        self.backend = _ShellBackend(directory.name)
        self.read = Read(backend=self.backend)
        self.edit = Edit(backend=self.backend)
        self.write = Write(backend=self.backend)
        self.initial_ns = 1_790_000_000_100_000_000

    def _put_file(self, content: str, mtime_ns: int) -> None:
        """Make an external change with an exact, deterministic timestamp."""
        self.path.write_text(content, encoding="utf-8")
        os.utime(self.path, ns=(mtime_ns, mtime_ns))

    async def _cache_file(self) -> tuple[str, AgentState]:
        """Populate the real Read cache before another actor changes a file."""
        self._put_file("agent=old\nhuman=old\n", self.initial_ns)
        path = str(self.path)
        state = AgentState()
        result = await self.read(file_path=path, _agent_state=state)
        self.assertEqual(result.state, "running")
        self.assertEqual(len(state.tool_context.read_file_cache), 1)
        return path, state

    def _external_change(self) -> None:
        """Change content and mtime while staying within the same second."""
        self._put_file(
            "agent=old\nhuman=NEW\n",
            self.initial_ns + 700_000_000,
        )

    async def test_stat_preserves_subsecond_changes(self) -> None:
        """Changes less than a second apart must have distinct cache keys."""
        mtimes = []
        for nanos in (100_000_000, 123_456_000, 123_456_500, 800_000_000):
            self._put_file("content", 1_790_000_000_000_000_000 + nanos)
            actual = await self.backend.stat_mtime(str(self.path))
            self.assertEqual(actual, self.path.stat().st_mtime)
            mtimes.append(actual)
        self.assertEqual(len(set(mtimes)), 4)

    async def test_zero_and_negative_epoch(self) -> None:
        """Preserve timestamps on either side of the epoch across dialects."""
        for nanos in (-1_100_000_000, -100_000_000, 0, 100_000_000):
            with self.subTest(nanos=nanos):
                self._put_file("content", nanos)
                if nanos < 0 and self.path.stat().st_mtime_ns != nanos:
                    self.skipTest("Negative mtime did not round-trip")
                actual = await self.backend.stat_mtime(str(self.path))
                self.assertIsNotNone(actual)
                self.assertAlmostEqual(actual, nanos / 1_000_000_000, places=9)

    async def test_symlink_uses_target_mtime(self) -> None:
        """A stable link must still observe its changing target's mtime."""
        self._put_file("before", self.initial_ns)
        link = self.directory / "linked.txt"
        link.symlink_to(self.path)
        link_mtime = link.lstat().st_mtime_ns
        before = await self.backend.stat_mtime(str(link))
        self.assertEqual(before, self.path.stat().st_mtime)
        self._put_file("after", self.initial_ns + 3_000_000_000)
        after = await self.backend.stat_mtime(str(link))
        self.assertEqual(after, self.path.stat().st_mtime)
        self.assertNotEqual(before, after)
        self.assertEqual(link.lstat().st_mtime_ns, link_mtime)

    async def test_missing_and_dangling_link(self) -> None:
        """BSD -L falls back to lstat, which must not validate a dead link."""
        self.assertIsNone(await self.backend.stat_mtime(str(self.path)))
        link = self.directory / "dangling.txt"
        link.symlink_to(self.path)
        self.assertIsNone(await self.backend.stat_mtime(str(link)))

    async def test_special_relative_path(self) -> None:
        """Leading dashes and shell syntax belong to the filename."""
        name = "-space ' quote $(touch injected) \n\u2603.txt"
        path = self.directory / name
        path.write_bytes(b"content")
        os.utime(path, ns=(self.initial_ns, self.initial_ns))
        self.assertEqual(
            await self.backend.stat_mtime(name),
            path.stat().st_mtime,
        )
        self.assertFalse((self.directory / "injected").exists())

    async def test_reread_then_edit_preserves_external_change(self) -> None:
        """Re-reading must refresh cached lines before Edit rebuilds a file."""
        path, state = await self._cache_file()
        self._external_change()
        result = await self.read(file_path=path, _agent_state=state)
        self.assertEqual(result.state, "running")
        self.assertIn("human=NEW", result.content[0].text)
        result = await self.edit(
            file_path=path,
            old_string="agent=old",
            new_string="agent=NEW",
            _agent_state=state,
        )
        self.assertEqual(result.state, "running")
        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            "agent=NEW\nhuman=NEW\n",
        )

    async def test_edit_rejects_stale_cache(self) -> None:
        """An external same-second write must require a new Read first."""
        path, state = await self._cache_file()
        self._external_change()
        result = await self.edit(
            file_path=path,
            old_string="agent=old",
            new_string="agent=NEW",
            _agent_state=state,
        )
        self.assertEqual(result.state, "error")
        self.assertIn("must first read", result.content[0].text)
        self.assertEqual(state.tool_context.read_file_cache, [])
        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            "agent=old\nhuman=NEW\n",
        )

    async def test_write_rejects_stale_cache(self) -> None:
        """Write must not accept an old Read as approval to replace changes."""
        path, state = await self._cache_file()
        self._external_change()
        result = await self.write(
            file_path=path,
            content="replacement\n",
            _agent_state=state,
        )
        self.assertEqual(result.state, "error")
        self.assertIn("read the file first", result.content[0].text)
        self.assertEqual(state.tool_context.read_file_cache, [])
        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            "agent=old\nhuman=NEW\n",
        )

    async def test_unchanged_cache_is_reused(self) -> None:
        """Higher precision must not turn every Read into a cache miss."""
        path, state = await self._cache_file()
        with patch.object(
            self.backend,
            "read_file",
            wraps=self.backend.read_file,
        ) as read_file:
            result = await self.read(file_path=path, _agent_state=state)
        self.assertEqual(result.state, "running")
        self.assertIn("human=old", result.content[0].text)
        read_file.assert_not_awaited()
        result = await self.write(
            file_path=path,
            content="replacement\n",
            _agent_state=state,
        )
        self.assertEqual(result.state, "running")
        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            "replacement\n",
        )
