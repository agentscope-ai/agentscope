# -*- coding: utf-8 -*-
"""Regression tests for shell subprocess pipe handling."""
import asyncio
import shlex
import subprocess
import sys
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.tool import execute_shell_command


def _python_command(code: str) -> str:
    """Quote a local Python command for the platform's default shell."""
    args = [sys.executable, "-c", code]
    if sys.platform == "win32":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


class ShellPipeTest(IsolatedAsyncioTestCase):
    """Exercise real pipe pressure without external programs or APIs."""

    async def test_large_output_does_not_deadlock(self) -> None:
        """Drain stdout, stderr, and both pipes beyond their buffer sizes."""
        size = 1024 * 1024
        for stdout_size, stderr_size in ((size, 0), (0, size), (size, size)):
            with self.subTest(stdout=stdout_size, stderr=stderr_size):
                code = (
                    "import sys; "
                    f"sys.stdout.write('O' * {stdout_size}); "
                    f"sys.stderr.write('E' * {stderr_size})"
                )
                result = await execute_shell_command(
                    _python_command(code),
                    timeout=5,
                )
                text = result.content[0]["text"]
                self.assertTrue(
                    text.startswith("<returncode>0</returncode>"),
                    text[:100],
                )
                self.assertEqual(
                    text,
                    "<returncode>0</returncode>"
                    f"<stdout>{'O' * stdout_size}</stdout>"
                    f"<stderr>{'E' * stderr_size}</stderr>",
                )

    async def test_timeout_preserves_buffered_output(self) -> None:
        """A timeout must not cancel the reader and discard consumed bytes."""
        size = 256 * 1024
        code = (
            "import sys, time; "
            f"sys.stdout.write('O' * {size}); sys.stdout.flush(); "
            f"sys.stderr.write('E' * {size}); sys.stderr.flush(); "
            "time.sleep(2)"
        )
        result = await execute_shell_command(_python_command(code), timeout=1)
        self.assertEqual(
            result.content[0]["text"],
            "<returncode>-1</returncode>"
            f"<stdout>{'O' * size}</stdout>"
            f"<stderr>{'E' * size}\n"
            "TimeoutError: The command execution exceeded "
            "the timeout of 1 seconds.</stderr>",
        )

    async def test_cancellation_cleans_up_communication(self) -> None:
        """Cancelling the tool terminates its process and awaits its reader."""
        started = asyncio.Event()
        stopped = asyncio.Event()
        finished = asyncio.Event()
        process = MagicMock(spec=asyncio.subprocess.Process)
        process.terminate.side_effect = stopped.set

        async def communicate() -> tuple[bytes, bytes]:
            """Finish reading once termination has been requested."""
            started.set()
            await stopped.wait()
            finished.set()
            return b"partial output", b""

        process.communicate = AsyncMock(side_effect=communicate)
        with patch(
            "agentscope.tool._coding._shell.asyncio.create_subprocess_shell",
            return_value=process,
        ):
            task = asyncio.create_task(execute_shell_command("unused"))
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=5)
                process.terminate.assert_called_once_with()
                process.communicate.assert_awaited_once_with()
                self.assertTrue(finished.is_set())
            finally:
                stopped.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_timeout_exit_race_keeps_output(self) -> None:
        """An already-exited process must not cause buffered output loss."""
        stopped = asyncio.Event()
        process = MagicMock(spec=asyncio.subprocess.Process)

        async def communicate() -> tuple[bytes, bytes]:
            """Return output after the timeout handler tries to terminate."""
            await stopped.wait()
            return b"stdout before timeout", b"stderr before timeout"

        def terminate() -> None:
            """Model a process exiting just before termination is attempted."""
            stopped.set()
            raise ProcessLookupError

        process.communicate = AsyncMock(side_effect=communicate)
        process.terminate.side_effect = terminate
        with patch(
            "agentscope.tool._coding._shell.asyncio.create_subprocess_shell",
            return_value=process,
        ):
            result = await execute_shell_command("unused", timeout=0)
        self.assertEqual(
            result.content[0]["text"],
            "<returncode>-1</returncode>"
            "<stdout>stdout before timeout</stdout>"
            "<stderr>stderr before timeout\n"
            "TimeoutError: The command execution exceeded "
            "the timeout of 0 seconds.</stderr>",
        )
