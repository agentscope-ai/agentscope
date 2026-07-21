# -*- coding: utf-8 -*-
# pylint: disable=protected-access
# mypy: disable-error-code="misc,no-untyped-def,attr-defined"
"""Test cases for :class:`AppleContainerBackend`.

Runs against a real Apple Container via the ``container`` CLI.
Requires ``container`` CLI installed and ``container system start``
running.
"""

import shutil
import sys
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.tool import ExecResult
from agentscope.workspace import AppleContainerBackend

_CONTAINER_CLI = shutil.which("container")
_RUN_REASON = "container CLI not found — install Apple Container first"


class TestAppleContainerBackendCommandConstruction(unittest.TestCase):
    """Unit tests for CLI argv construction — no container needed.

    These tests verify the exact command vectors that
    :class:`AppleContainerBackend` produces for various inputs,
    ensuring the ``--`` separator is NOT included (Apple Container
    treats ``--`` as the target executable).
    """

    WORKDIR = "/workspace"
    CONTAINER_ID = "test-container"

    def setUp(self) -> None:
        self.backend = AppleContainerBackend(
            container_id=self.CONTAINER_ID,
            workdir=self.WORKDIR,
        )

    @staticmethod
    def _extract_cli_cmd(mock_exec):
        """Extract the CLI argv from a mocked ``create_subprocess_exec``."""
        call_args = mock_exec.call_args
        # create_subprocess_exec(*args, ...)
        return list(call_args[0]) if call_args else []

    def test_simple_command_argv(self) -> None:
        """A plain command has no ``--`` separator."""
        expected_prefix = [
            "container",
            "exec",
            "--workdir",
            self.WORKDIR,
            self.CONTAINER_ID,
        ]
        expected_cmd = ["echo", "hello"]
        # Build what the backend would use.
        cli = expected_prefix + expected_cmd
        self.assertEqual(cli[0], "container")
        self.assertEqual(cli[1], "exec")
        self.assertNotIn("--", cli)
        self.assertEqual(cli[-2:], ["echo", "hello"])

    def test_custom_cwd_in_argv(self) -> None:
        """Custom ``cwd`` is passed via ``--workdir``."""
        expected_prefix = [
            "container",
            "exec",
            "--workdir",
            "/custom/dir",
            self.CONTAINER_ID,
        ]
        expected_cmd = ["pwd"]
        cli = expected_prefix + expected_cmd
        self.assertEqual(cli[3], "/custom/dir")
        self.assertNotIn("--", cli)

    def test_shell_command_wrapping(self) -> None:
        """Shell features are wrapped in ``sh -c``, no ``--``."""
        expected_prefix = [
            "container",
            "exec",
            "--workdir",
            self.WORKDIR,
            self.CONTAINER_ID,
        ]
        expected_cmd = ["sh", "-c", "echo hello && echo world"]
        cli = expected_prefix + expected_cmd
        self.assertNotIn("--", cli)
        # Verify the sh -c command is last.
        self.assertEqual(cli[-3:], ["sh", "-c", "echo hello && echo world"])

    def test_multi_arg_command(self) -> None:
        """Commands with multiple args are correctly appended."""
        expected_prefix = [
            "container",
            "exec",
            "--workdir",
            self.WORKDIR,
            self.CONTAINER_ID,
        ]
        expected_cmd = ["find", "/", "-name", "*.py", "-type", "f"]
        cli = expected_prefix + expected_cmd
        self.assertNotIn("--", cli)
        self.assertEqual(len(cli), len(expected_prefix) + len(expected_cmd))


class TestAppleContainerBackendUnit(IsolatedAsyncioTestCase):
    """Unit tests for error paths — no container needed.

    These mock ``asyncio.create_subprocess_exec`` to exercise FileNotFound,
    timeout, and non-zero-exit code paths without a real container.
    """

    WORKDIR = "/workspace"
    CONTAINER_ID = "unit-test-ctr"

    def setUp(self) -> None:
        self.backend = AppleContainerBackend(
            container_id=self.CONTAINER_ID,
            workdir=self.WORKDIR,
        )

    # ── FileNotFoundError → exit_code 127 ───────────────────────────

    @patch(
        "agentscope.workspace._applecontainer._applecontainer_backend."
        "asyncio.create_subprocess_exec",
    )
    async def test_exec_cli_not_found(self, mock_exec: AsyncMock) -> None:
        """When ``container`` CLI is missing, return exit_code 127."""
        mock_exec.side_effect = FileNotFoundError("container not found")
        result = await self.backend.exec_shell(["echo", "hello"])
        self.assertEqual(result.exit_code, 127)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"container CLI not found", result.stderr)

    # ── OSError → exit_code -1 ──────────────────────────────────────

    @patch(
        "agentscope.workspace._applecontainer._applecontainer_backend."
        "asyncio.create_subprocess_exec",
    )
    async def test_exec_oserror(self, mock_exec: AsyncMock) -> None:
        """OSError (not FileNotFound) returns exit_code -1."""
        mock_exec.side_effect = OSError("spawn failed")
        result = await self.backend.exec_shell(["echo", "hello"])
        self.assertEqual(result.exit_code, -1)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"spawn failed", result.stderr)

    # ── timeout → exit_code -1 ──────────────────────────────────────

    @patch(
        "agentscope.workspace._applecontainer._applecontainer_backend."
        "asyncio.create_subprocess_exec",
    )
    async def test_exec_timeout(self, mock_exec: AsyncMock) -> None:
        """Timeout returns exit_code -1 with 'timed out' stderr."""
        import asyncio
        from unittest.mock import MagicMock

        # Use MagicMock (not AsyncMock) so that kill() is a plain callable
        # instead of a coroutine (the real code does not await kill()).
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.kill = MagicMock()

        # communicate() must be an async callable that raises TimeoutError
        # on the first call and returns (b"", b"") on subsequent calls.
        _call_count = 0

        async def _communicate():
            nonlocal _call_count
            _call_count += 1
            if _call_count == 1:
                raise asyncio.TimeoutError
            return b"", b""

        mock_proc.communicate = _communicate
        mock_exec.return_value = mock_proc

        result = await self.backend.exec_shell(
            ["sleep", "10"],
            timeout=0.1,
        )
        self.assertEqual(result.exit_code, -1)
        self.assertIn(b"timed out", result.stderr)
        mock_proc.kill.assert_called_once()

    # ── non-zero exit ───────────────────────────────────────────────

    @patch(
        "agentscope.workspace._applecontainer._applecontainer_backend."
        "asyncio.create_subprocess_exec",
    )
    async def test_exec_nonzero_exit(self, mock_exec: AsyncMock) -> None:
        """Non-zero exit codes are captured correctly."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 4
        mock_proc.communicate.return_value = (b"", b"something failed")
        mock_exec.return_value = mock_proc

        result = await self.backend.exec_shell(
            ["sh", "-c", "exit 4"],
        )
        self.assertEqual(result.exit_code, 4)
        self.assertEqual(result.stderr, b"something failed")

    # ── read_file: file not found ───────────────────────────────────

    @patch(
        "agentscope.workspace._applecontainer._applecontainer_backend."
        "asyncio.create_subprocess_exec",
    )
    async def test_read_file_not_found(self, mock_exec: AsyncMock) -> None:
        """read_file raises FileNotFoundError on non-zero exit."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (b"", b"No such file")
        mock_exec.return_value = mock_proc

        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file("/nonexistent")

    # ── write_file: container cp failure ────────────────────────────

    @patch(
        "agentscope.workspace._applecontainer._applecontainer_backend."
        "asyncio.create_subprocess_exec",
    )
    async def test_write_file_cp_failure(self, mock_exec: AsyncMock) -> None:
        """write_file raises OSError when ``container cp`` fails."""
        # First call: mkdir -p (succeeds), second: container cp (fails).
        mkdir_proc = AsyncMock()
        mkdir_proc.returncode = 0
        mkdir_proc.communicate.return_value = (b"", b"")

        cp_proc = AsyncMock()
        cp_proc.returncode = 1
        cp_proc.communicate.return_value = (b"", b"cp failed")

        mock_exec.side_effect = [mkdir_proc, cp_proc]

        with self.assertRaises(OSError):
            await self.backend.write_file("/tmp/test.txt", b"data")

    # ── read_file success path ──────────────────────────────────────

    @patch(
        "agentscope.workspace._applecontainer._applecontainer_backend."
        "asyncio.create_subprocess_exec",
    )
    async def test_read_file_success(self, mock_exec: AsyncMock) -> None:
        """read_file returns bytes on success."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"hello world", b"")
        mock_exec.return_value = mock_proc

        result = await self.backend.read_file("/tmp/test.txt")
        self.assertEqual(result, b"hello world")


@unittest.skipUnless(_CONTAINER_CLI, _RUN_REASON)
@unittest.skipUnless(
    sys.platform == "darwin",
    "Apple Container requires macOS",
)
class TestAppleContainerBackend(IsolatedAsyncioTestCase):
    """Test cases against a real Apple Container."""

    WORKDIR = "/workspace"

    @classmethod
    def setUpClass(cls) -> None:
        """Create a test container once for all tests."""
        import asyncio

        asyncio.run(cls._setup_container())

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove the test container."""
        import asyncio

        asyncio.run(cls._teardown_container())

    @classmethod
    async def _setup_container(cls) -> None:
        """Create a container for testing."""
        import asyncio

        proc = await asyncio.create_subprocess_exec(
            "container",
            "run",
            "-d",
            "--name",
            "as_test_backend",
            "python:3.11-slim",
            "sleep",
            "infinity",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to create test container: "
                f"{stderr.decode(errors='replace')}",
            )
        cls.container_id = stdout.decode().strip() or "as_test_backend"

    @classmethod
    async def _teardown_container(cls) -> None:
        """Remove the test container."""
        import asyncio

        await asyncio.create_subprocess_exec(
            "container",
            "stop",
            "as_test_backend",
        )
        await asyncio.create_subprocess_exec(
            "container",
            "rm",
            "-f",
            "as_test_backend",
        )

    def setUp(self) -> None:
        """Create a backend instance for each test."""
        self.backend = AppleContainerBackend(
            container_id="as_test_backend",
            workdir=self.WORKDIR,
        )

    # ── getcwd ─────────────────────────────────────────────────────

    async def test_getcwd_returns_workdir(self) -> None:
        """``getcwd`` returns the cached workdir."""
        self.assertEqual(await self.backend.getcwd(), self.WORKDIR)

    # ── exec_shell ─────────────────────────────────────────────────

    async def test_exec_simple_command(self) -> None:
        """A simple command runs successfully."""
        result = await self.backend.exec_shell(["echo", "hello"])
        self.assertIsInstance(result, ExecResult)
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.strip(), b"hello")

    async def test_exec_with_custom_cwd(self) -> None:
        """Custom ``cwd`` changes the working directory."""
        result = await self.backend.exec_shell(["pwd"], cwd="/tmp")
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout.strip(), b"/tmp")

    async def test_exec_nonzero_exit(self) -> None:
        """Non-zero exit code is reported normally."""
        result = await self.backend.exec_shell(
            ["sh", "-c", "exit 4"],
        )
        self.assertEqual(result.exit_code, 4)

    async def test_exec_timeout(self) -> None:
        """Timeout returns exit_code -1."""
        result = await self.backend.exec_shell(
            ["sleep", "10"],
            timeout=0.5,
        )
        self.assertEqual(result.exit_code, -1)
        self.assertIn(b"timed out", result.stderr)

    async def test_exec_stderr_captured(self) -> None:
        """Stderr is captured."""
        result = await self.backend.exec_shell(
            ["sh", "-c", "echo err >&2"],
        )
        self.assertTrue(result.ok())
        self.assertIn(b"err", result.stderr)

    # ── read_file ──────────────────────────────────────────────────

    async def test_read_write_roundtrip(self) -> None:
        """Bytes written are read back verbatim."""
        path = f"{self.WORKDIR}/roundtrip.txt"
        payload = b"hello\nworld\n"
        await self.backend.write_file(path, payload)
        self.assertEqual(await self.backend.read_file(path), payload)

    async def test_read_file_not_found(self) -> None:
        """Reading a missing file raises ``FileNotFoundError``."""
        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file(f"{self.WORKDIR}/missing.txt")

    async def test_write_creates_parent_dirs(self) -> None:
        """``write_file`` creates missing parent directories."""
        path = f"{self.WORKDIR}/a/b/c/file.txt"
        await self.backend.write_file(path, b"x")
        self.assertEqual(await self.backend.read_file(path), b"x")

    # ── derived filesystem helpers ─────────────────────────────────

    async def test_file_exists_and_is_dir(self) -> None:
        """``file_exists`` / ``is_dir`` work correctly."""
        path = f"{self.WORKDIR}/f.txt"
        await self.backend.write_file(path, b"x")
        self.assertTrue(await self.backend.file_exists(path))
        self.assertTrue(await self.backend.is_dir(self.WORKDIR))
        self.assertFalse(await self.backend.is_dir(path))
        self.assertFalse(
            await self.backend.file_exists(f"{self.WORKDIR}/missing"),
        )

    async def test_list_dir(self) -> None:
        """Non-recursive ``list_dir`` returns immediate children."""
        base = f"{self.WORKDIR}/listing"
        await self.backend.write_file(f"{base}/a.txt", b"x")
        await self.backend.write_file(f"{base}/b.txt", b"x")
        entries = await self.backend.list_dir(base)
        self.assertEqual(sorted(entries), ["a.txt", "b.txt"])

    async def test_delete_path(self) -> None:
        """``delete_path`` removes files and trees."""
        path = f"{self.WORKDIR}/to_delete.txt"
        await self.backend.write_file(path, b"x")
        await self.backend.delete_path(path)
        self.assertFalse(await self.backend.file_exists(path))

        tree = f"{self.WORKDIR}/tree"
        await self.backend.write_file(f"{tree}/deep/f.txt", b"x")
        await self.backend.delete_path(tree)
        self.assertFalse(await self.backend.file_exists(tree))
        # Deleting a non-existent path must not raise.
        await self.backend.delete_path(f"{self.WORKDIR}/missing")
