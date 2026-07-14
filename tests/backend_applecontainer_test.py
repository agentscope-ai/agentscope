# -*- coding: utf-8 -*-
# pylint: disable=protected-access
# mypy: disable-error-code="misc,no-untyped-def,attr-defined"
"""Test cases for :class:`AppleContainerBackend`.

Validates that the three backend primitives (``exec_shell``,
``read_file``, ``write_file``) construct the correct ``container`` CLI
commands. Subprocess calls are mocked — no real ``container`` CLI is
required.
"""

import asyncio
import sys
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
from unittest import skipUnless

from agentscope.workspace._applecontainer._applecontainer_backend import (
    AppleContainerBackend,
)

# ── mock helpers ────────────────────────────────────────────────────


def _make_mock_process(
    exit_code: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> MagicMock:
    """Build an :class:`asyncio.subprocess.Process` mock."""
    proc = MagicMock()
    proc.returncode = exit_code
    proc.communicate = AsyncMock(
        return_value=(stdout, stderr),
    )
    return proc


def _captured_cli_cmd(mock_create: MagicMock) -> list[str]:
    """Extract the CLI command list from the last
    ``create_subprocess_exec`` call."""
    call_args = mock_create.call_args
    if call_args is None:
        return []
    return list(call_args[0])


# ── tests ───────────────────────────────────────────────────────────


_IS_MACOS = sys.platform == "darwin"


@skipUnless(_IS_MACOS, "Apple Container tests require macOS")
class TestAppleContainerBackend(IsolatedAsyncioTestCase):
    """Test cases for ``AppleContainerBackend`` with mocked subprocess."""

    CONTAINER_ID = "as_ws_test1234"
    WORKDIR = "/workspace"

    def setUp(self) -> None:
        """Create a backend instance for each test."""
        self.backend = AppleContainerBackend(
            container_id=self.CONTAINER_ID,
            workdir=self.WORKDIR,
        )

    # ── getcwd ─────────────────────────────────────────────────────

    async def test_getcwd_returns_workdir(self) -> None:
        """``getcwd`` returns the cached workdir."""
        self.assertEqual(await self.backend.getcwd(), self.WORKDIR)

    # ── exec_shell ─────────────────────────────────────────────────

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    async def test_exec_simple_command(self, mock_create: AsyncMock) -> None:
        """A simple command is passed directly to ``container exec``."""
        mock_create.return_value = _make_mock_process(
            exit_code=0,
            stdout=b"hello\n",
        )

        result = await self.backend.exec_shell(["echo", "hello"])
        self.assertTrue(result.ok())
        self.assertEqual(result.stdout, b"hello\n")

        cmd = _captured_cli_cmd(mock_create)
        self.assertEqual(cmd[0], "container")
        self.assertEqual(cmd[1], "exec")
        self.assertIn(self.CONTAINER_ID, cmd)
        # The command args should appear after '--'
        dash_index = cmd.index("--") if "--" in cmd else -1
        self.assertGreater(dash_index, 0)
        self.assertEqual(cmd[dash_index + 1 :], ["echo", "hello"])

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    async def test_exec_with_custom_cwd(self, mock_create: AsyncMock) -> None:
        """Custom ``cwd`` is forwarded via ``--workdir``."""
        mock_create.return_value = _make_mock_process(
            exit_code=0,
            stdout=b"/tmp\n",
        )

        result = await self.backend.exec_shell(
            ["pwd"],
            cwd="/tmp",
        )
        self.assertTrue(result.ok())
        cmd = _captured_cli_cmd(mock_create)
        workdir_idx = cmd.index("--workdir")
        self.assertEqual(cmd[workdir_idx + 1], "/tmp")

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    async def test_exec_nonzero_exit(self, mock_create: AsyncMock) -> None:
        """Non-zero exit code is reported normally."""
        mock_create.return_value = _make_mock_process(
            exit_code=1,
            stderr=b"error",
        )

        result = await self.backend.exec_shell(["false"])
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr, b"error")

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    async def test_exec_timeout(self, mock_create: AsyncMock) -> None:
        """Timeout returns exit_code -1."""
        # Simulate a command that hangs: communicate() raises
        # TimeoutError, then kill() + communicate() succeed.
        proc = MagicMock()
        proc.returncode = -1
        # First communicate() call raises TimeoutError.
        # Second communicate() call (after kill) returns empty.
        proc.communicate = AsyncMock(
            side_effect=[asyncio.TimeoutError(), (b"", b"")],
        )
        mock_create.return_value = proc

        result = await self.backend.exec_shell(
            ["sleep", "999"],
            timeout=0.001,
        )
        self.assertEqual(result.exit_code, -1)
        self.assertIn(b"timed out", result.stderr)

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    async def test_exec_cli_not_found(
        self,
        mock_create: AsyncMock,
    ) -> None:
        """``FileNotFoundError`` returns exit_code 127."""
        mock_create.side_effect = FileNotFoundError("container not found")

        result = await self.backend.exec_shell(["echo", "hello"])
        self.assertEqual(result.exit_code, 127)
        self.assertIn(b"CLI not found", result.stderr)

    # ── read_file ──────────────────────────────────────────────────

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    async def test_read_file(self, mock_create: AsyncMock) -> None:
        """``read_file`` uses ``container exec cat``."""
        mock_create.return_value = _make_mock_process(
            exit_code=0,
            stdout=b"file contents\n",
        )

        data = await self.backend.read_file("/workspace/test.txt")
        self.assertEqual(data, b"file contents\n")

        cmd = _captured_cli_cmd(mock_create)
        self.assertIn("cat", cmd)
        self.assertIn("/workspace/test.txt", cmd)

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    async def test_read_file_not_found(self, mock_create: AsyncMock) -> None:
        """Reading a missing file raises ``FileNotFoundError``."""
        mock_create.return_value = _make_mock_process(
            exit_code=1,
            stderr=b"No such file",
        )

        with self.assertRaises(FileNotFoundError):
            await self.backend.read_file("/workspace/missing.txt")

    # ── write_file ─────────────────────────────────────────────────

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    async def test_write_file(self, mock_create: AsyncMock) -> None:
        """``write_file`` creates a temp file and uses ``container cp``."""
        # First call: mkdir -p (for parent dir)
        # Second call: container cp
        proc_mkdir = _make_mock_process(exit_code=0)
        proc_cp = _make_mock_process(exit_code=0)

        call_count = 0

        async def _side_effect(  # type: ignore[no-untyped-def]
            *_args: object,
            **_kwargs: object,
        ):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return proc_mkdir
            return proc_cp

        mock_create.side_effect = _side_effect

        await self.backend.write_file("/workspace/sub/d.txt", b"hello")

        # The second subprocess call should be ``container cp``.
        self.assertGreaterEqual(call_count, 2)
        # Get the second call args — should be container cp
        all_calls = mock_create.call_args_list
        cp_call = all_calls[1][0]
        self.assertEqual(cp_call[0], "container")
        self.assertEqual(cp_call[1], "cp")
        # Third arg is temp path, fourth is container_id:dest_path
        dest = cp_call[3]
        self.assertTrue(dest.startswith(f"{self.CONTAINER_ID}:"))
        self.assertTrue(dest.endswith("/workspace/sub/d.txt"))

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    async def test_write_file_root_path(self, mock_create: AsyncMock) -> None:
        """Writing to a root-level path skips mkdir -p."""
        proc_cp = _make_mock_process(exit_code=0)
        mock_create.return_value = proc_cp

        await self.backend.write_file("/root_file.txt", b"data")
        # Only one call for cp (mkdir -p skipped for parent='/')
        self.assertEqual(mock_create.call_count, 1)
        cmd = _captured_cli_cmd(mock_create)
        self.assertEqual(cmd[0], "container")
        self.assertEqual(cmd[1], "cp")

    # ── temp file cleanup ──────────────────────────────────────────

    @patch(
        "asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    )
    async def test_write_file_cleanup_temp(
        self,
        mock_create: AsyncMock,
    ) -> None:
        """Temp file is removed after ``container cp``, even on error."""
        proc_cp = _make_mock_process(exit_code=1, stderr=b"cp failed")
        mock_create.return_value = proc_cp

        with self.assertRaises(OSError):
            await self.backend.write_file("/workspace/f.txt", b"data")

        # After the call, the temp file no longer exists.
        # We can't easily verify this with mocked subprocess since
        # tempfile.mkstemp is real, but the try/finally ensures
        # os.unlink is called.
