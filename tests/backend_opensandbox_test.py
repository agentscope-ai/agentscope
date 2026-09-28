# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for :class:`OpenSandboxBackend`.

Validates that the three backend primitives (``exec_shell``,
``read_file``, ``write_file``) and the inherited shell-based filesystem
helpers behave correctly inside a real OpenSandbox sandbox.

Most of the module is skipped unless the ``OPENSANDBOX_DOMAIN`` environment
variable is set, because those tests require a live OpenSandbox service.
CI runs without OpenSandbox access are therefore unaffected; when a
domain *is* present the tests exercise the real ``commands.run`` /
``files.*`` APIs.

``TestOpenSandboxWriteEntry`` runs everywhere: it only inspects the write
entry the backend builds, so it needs no live service.

A live sandbox is obtained by initializing an :class:`OpenSandboxWorkspace`
and reusing its already-wired :class:`OpenSandboxBackend` (``ws._backend``),
which avoids duplicating the sandbox bring-up logic here.
"""

import os
import unittest
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import ExecResult
from agentscope.workspace import OpenSandboxWorkspace
from agentscope.workspace import OpenSandboxBackend
from agentscope.workspace._opensandbox._constants import SANDBOX_WORKDIR

# ── OpenSandbox availability check ─────────────────────────────────

_DOMAIN = os.getenv("OPENSANDBOX_DOMAIN", "")
_API_KEY = os.getenv("OPENSANDBOX_API_KEY", "")
_SKIP_REASON = "OPENSANDBOX_DOMAIN environment variable is not set"


@unittest.skipUnless(_DOMAIN, _SKIP_REASON)
class TestOpenSandboxBackend(IsolatedAsyncioTestCase):
    """Test cases for ``OpenSandboxBackend`` against a live sandbox.

    Each test creates a real OpenSandbox sandbox via
    ``OpenSandboxWorkspace`` and tears it down (``close`` → sandbox
    pause) afterwards.
    """

    async def asyncSetUp(self) -> None:
        """Start a workspace and reuse its wired backend."""
        self.workspace = OpenSandboxWorkspace(
            domain=_DOMAIN,
            api_key=_API_KEY,
        )
        await self.workspace.initialize()
        self.backend = self.workspace._backend
        self.assertIsInstance(self.backend, OpenSandboxBackend)

    async def asyncTearDown(self) -> None:
        """Pause / close the sandbox."""
        await self.workspace.close()

    # ── exec ───────────────────────────────────────────────────────

    async def test_exec_returns_stdout(self) -> None:
        """A program's stdout/exit code are captured into ``ExecResult``."""
        result = await self.backend.exec_shell(["echo", "hello world"])
        self.assertIsInstance(result, ExecResult)
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().strip(), "hello world")

    async def test_exec_nonzero_exit(self) -> None:
        """A non-zero command exit is reported as a normal result."""
        result = await self.backend.exec_shell(
            ["sh", "-c", "echo oops >&2; exit 4"],
        )
        self.assertEqual(result.exit_code, 4)
        self.assertIn("oops", result.stderr.decode())

    async def test_exec_argv_quoting_preserved(self) -> None:
        """An argv element with spaces / metacharacters survives the
        POSIX-quote round-trip the backend does for ``commands.run``."""
        tricky = "a b c | ;"
        result = await self.backend.exec_shell(["echo", tricky])
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().rstrip("\n"), tricky)

    async def test_exec_cwd_default_is_workdir(self) -> None:
        """With no explicit ``cwd`` the sandbox workdir is used."""
        result = await self.backend.exec_shell(["pwd"])
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.decode().strip(), SANDBOX_WORKDIR)

    # ── file I/O ───────────────────────────────────────────────────

    async def test_write_then_read_roundtrip(self) -> None:
        """Bytes written into the sandbox are read back verbatim."""
        path = f"{SANDBOX_WORKDIR}/roundtrip.txt"
        payload = b"hello\nworld\n"
        await self.backend.write_file(path, payload)
        self.assertEqual(await self.backend.read_file(path), payload)

    async def test_write_creates_parent_dirs(self) -> None:
        """``write_file`` creates missing parent directories."""
        path = f"{SANDBOX_WORKDIR}/a/b/c/file.txt"
        await self.backend.write_file(path, b"x")
        self.assertEqual(await self.backend.read_file(path), b"x")

    async def test_read_missing_file_raises(self) -> None:
        """Reading a non-existent file raises ``FileNotFoundError``."""
        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file(f"{SANDBOX_WORKDIR}/nope.txt")

    # ── derived filesystem helpers (shell-based) ───────────────────

    async def test_file_exists_and_is_dir(self) -> None:
        """``file_exists`` / ``is_dir`` reflect the sandbox filesystem."""
        path = f"{SANDBOX_WORKDIR}/f.txt"
        await self.backend.write_file(path, b"x")
        self.assertTrue(await self.backend.file_exists(path))
        self.assertTrue(await self.backend.is_dir(SANDBOX_WORKDIR))
        self.assertFalse(await self.backend.is_dir(path))
        self.assertFalse(
            await self.backend.file_exists(f"{SANDBOX_WORKDIR}/missing"),
        )

    async def test_list_dir(self) -> None:
        """Non-recursive ``list_dir`` returns immediate child base names."""
        base = f"{SANDBOX_WORKDIR}/listing"
        await self.backend.write_file(f"{base}/a.txt", b"x")
        await self.backend.write_file(f"{base}/b.txt", b"x")
        entries = await self.backend.list_dir(base)
        self.assertEqual(sorted(entries), ["a.txt", "b.txt"])

    async def test_stat_mtime(self) -> None:
        """``stat_mtime`` returns a float for an existing path, None else."""
        path = f"{SANDBOX_WORKDIR}/stat.txt"
        await self.backend.write_file(path, b"x")
        mtime = await self.backend.stat_mtime(path)
        self.assertIsInstance(mtime, float)
        self.assertIsNone(
            await self.backend.stat_mtime(f"{SANDBOX_WORKDIR}/missing"),
        )

    async def test_delete_path(self) -> None:
        """``delete_path`` removes files and trees; missing is a no-op."""
        path = f"{SANDBOX_WORKDIR}/to_delete.txt"
        await self.backend.write_file(path, b"x")
        await self.backend.delete_path(path)
        self.assertFalse(await self.backend.file_exists(path))

        tree = f"{SANDBOX_WORKDIR}/tree"
        await self.backend.write_file(f"{tree}/deep/f.txt", b"x")
        await self.backend.delete_path(tree)
        self.assertFalse(await self.backend.file_exists(tree))

        # Deleting a non-existent path must not raise.
        await self.backend.delete_path(f"{SANDBOX_WORKDIR}/missing")


# ── write entry (no live sandbox required) ─────────────────────────


class _FakeExecResult:
    """Minimal ``commands.run`` result shape."""

    exit_code = 0
    stdout = b""
    stderr = b""


class _FakeCommands:
    """Records the command lines the backend dispatches."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(
        self, command_line: str, opts: Any = None
    ) -> _FakeExecResult:
        """Record *command_line* and report a successful execution."""
        self.calls.append(command_line)
        return _FakeExecResult()


class _FakeFiles:
    """Records the write entries handed to ``files.write_files``."""

    def __init__(self) -> None:
        self.entries: list[Any] = []

    async def write_files(self, entries: list[Any]) -> None:
        """Record *entries* instead of writing them anywhere."""
        self.entries.extend(entries)


class _FakeSandbox:
    """Stand-in for ``opensandbox.sandbox.Sandbox``."""

    def __init__(self) -> None:
        self.commands = _FakeCommands()
        self.files = _FakeFiles()


class TestOpenSandboxWriteEntry(IsolatedAsyncioTestCase):
    """Test cases for the write entry ``write_file`` builds.

    OpenSandbox's ``WriteEntry.mode`` carries the permission bits as an
    integer whose *decimal digits* are the octal mode (the SDK default is
    ``755``, and execd parses the value as octal). Sending ``0o644``
    instead puts ``420`` on the wire, which execd reads as ``0o420`` —
    owner read-only, so the second write to the same file fails with
    ``permission denied``. These tests pin the decimal encoding without
    needing a live sandbox.
    """

    def test_write_entry_mode_is_decimal_encoded(self) -> None:
        """``_make_write_entry`` asks for ``0o644`` as the integer 644."""
        entry = OpenSandboxBackend._make_write_entry("/workspace/a.txt", b"x")
        self.assertEqual(entry.mode, 644)
        self.assertEqual(oct(int(str(entry.mode), 8)), "0o644")

    async def test_write_file_sends_that_mode(self) -> None:
        """``write_file`` passes the mode through to ``files.write_files``."""
        sandbox = _FakeSandbox()
        # The fake only implements the two calls this path makes.
        backend = OpenSandboxBackend(
            sandbox,  # type: ignore[arg-type]
            SANDBOX_WORKDIR,
        )

        path = f"{SANDBOX_WORKDIR}/a/b.txt"
        await backend.write_file(path, b"payload")

        self.assertEqual(sandbox.commands.calls[0], "mkdir -p /workspace/a")
        self.assertEqual(len(sandbox.files.entries), 1)
        entry = sandbox.files.entries[0]
        self.assertEqual(entry.path, path)
        self.assertEqual(entry.data, b"payload")
        self.assertEqual(entry.mode, 644)
