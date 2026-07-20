# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""PowerShell tool test cases."""

import sys
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.permission import PermissionBehavior, PermissionContext
from agentscope.tool import ExecResult, LocalBackend, PowerShell


class PowerShellInterfaceTest(IsolatedAsyncioTestCase):
    """Test the public PowerShell interface and conservative permissions."""

    async def test_public_interface_is_conservative(self) -> None:
        """Expose the command schema without auto-allowing any command."""
        backend = LocalBackend()
        tool = PowerShell(cwd="workspace", backend=backend)

        self.assertEqual(tool.name, "PowerShell")
        self.assertFalse(tool.is_read_only)
        self.assertFalse(tool.is_concurrency_safe)
        self.assertIn("command", tool.input_schema["properties"])
        self.assertEqual(tool.input_schema["required"], ["command"])
        self.assertEqual(tool._cwd, "workspace")
        self.assertIs(tool._backend, backend)

        decision = await tool.check_permissions(
            {"command": "Get-Location"},
            PermissionContext(),
        )
        self.assertEqual(
            decision.behavior,
            PermissionBehavior.PASSTHROUGH,
        )
        self.assertEqual(
            await tool.generate_suggestions(
                {"command": "Get-Location"},
            ),
            [],
        )


class PowerShellExecutionTest(IsolatedAsyncioTestCase):
    """Test PowerShell command execution through the backend."""

    async def test_call_uses_noninteractive_powershell_and_cwd(self) -> None:
        """Wrap commands for PowerShell and pass cwd to the backend."""
        backend = AsyncMock()
        backend.exec_shell.return_value = ExecResult(0, b"ok\r\n", b"")
        tool = PowerShell(cwd="workspace", backend=backend)

        chunks = [
            chunk
            async for chunk in await tool(
                command="Get-Location",
            )
        ]

        argv = backend.exec_shell.await_args.args[0]
        self.assertEqual(
            argv[:-1],
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
            ],
        )
        self.assertIn("[Console]::OutputEncoding", argv[-1])
        self.assertTrue(argv[-1].endswith("Get-Location"))
        self.assertEqual(
            backend.exec_shell.await_args.kwargs,
            {"cwd": "workspace", "timeout": 120.0},
        )
        self.assertEqual(chunks[0].content[0].text, "ok\n")
        self.assertEqual(chunks[0].state, "running")

    async def test_nonzero_exit_returns_error_with_both_streams(self) -> None:
        """Report stdout and stderr when PowerShell exits unsuccessfully."""
        backend = AsyncMock()
        backend.exec_shell.return_value = ExecResult(
            3,
            b"partial\r\n",
            b"failed\r\n",
        )
        tool = PowerShell(backend=backend)

        chunks = [
            chunk
            async for chunk in await tool(
                command="Write-Error 'failed'; exit 3",
            )
        ]

        self.assertEqual(chunks[0].state, "error")
        self.assertEqual(
            chunks[0].content[0].text,
            "Command failed: Write-Error 'failed'; exit 3\n"
            "\nStdout:\npartial\n"
            "\nStderr:\nfailed\n",
        )

    async def test_timeout_returns_specific_error(self) -> None:
        """Translate the backend timeout sentinel into a clear message."""
        backend = AsyncMock()
        backend.exec_shell.return_value = ExecResult(
            -1,
            b"",
            b"timed out",
        )
        tool = PowerShell(backend=backend)

        chunks = [
            chunk
            async for chunk in await tool(
                command="Start-Sleep -Seconds 5",
                timeout=100,
            )
        ]

        self.assertEqual(chunks[0].state, "error")
        self.assertEqual(
            chunks[0].content[0].text,
            "Command timed out after 100ms: Start-Sleep -Seconds 5",
        )

    async def test_success_preserves_unicode_and_normalizes_newlines(
        self,
    ) -> None:
        """Decode UTF-8 and normalize CRLF and lone CR line endings."""
        backend = AsyncMock()
        backend.exec_shell.return_value = ExecResult(
            0,
            "你好\r\nPowerShell\rAgentScope".encode("utf-8"),
            b"",
        )
        tool = PowerShell(backend=backend)

        chunks = [
            chunk
            async for chunk in await tool(
                command="Write-Output '你好'",
            )
        ]

        self.assertEqual(
            chunks[0].content[0].text,
            "你好\nPowerShell\nAgentScope",
        )

    async def test_success_output_is_truncated(self) -> None:
        """Cap large PowerShell output to the builtin tool limit."""
        backend = AsyncMock()
        backend.exec_shell.return_value = ExecResult(
            0,
            b"x" * 30001,
            b"",
        )
        tool = PowerShell(backend=backend)

        chunks = [
            chunk
            async for chunk in await tool(
                command="Write-Output ('x' * 30001)",
            )
        ]

        self.assertEqual(
            chunks[0].content[0].text,
            "x" * 30000 + "\n... (output truncated)",
        )

    async def test_success_includes_stderr(self) -> None:
        """Preserve stderr even when the command exits successfully."""
        backend = AsyncMock()
        backend.exec_shell.return_value = ExecResult(
            0,
            b"output",
            b"warning",
        )
        tool = PowerShell(backend=backend)

        chunks = [
            chunk
            async for chunk in await tool(
                command="native-command",
            )
        ]

        self.assertEqual(
            chunks[0].content[0].text,
            "output\nwarning",
        )

    async def test_error_output_is_truncated(self) -> None:
        """Apply the output limit to failed commands as well."""
        backend = AsyncMock()
        backend.exec_shell.return_value = ExecResult(
            1,
            b"",
            b"x" * 30001,
        )
        tool = PowerShell(backend=backend)

        chunks = [
            chunk
            async for chunk in await tool(
                command="Write-Error 'x'",
            )
        ]

        suffix = "\n... (output truncated)"
        text = chunks[0].content[0].text
        self.assertEqual(chunks[0].state, "error")
        self.assertTrue(text.endswith(suffix))
        self.assertEqual(len(text), 30000 + len(suffix))

    async def test_backend_exception_returns_error_chunk(self) -> None:
        """Convert unexpected backend failures into tool errors."""
        backend = AsyncMock()
        backend.exec_shell.side_effect = RuntimeError("backend unavailable")
        tool = PowerShell(backend=backend)

        chunks = [
            chunk
            async for chunk in await tool(
                command="Get-Location",
            )
        ]

        self.assertEqual(chunks[0].state, "error")
        self.assertEqual(
            chunks[0].content[0].text,
            "Command failed: Get-Location\nError: backend unavailable",
        )

    async def test_timeout_is_capped_at_ten_minutes(self) -> None:
        """Clamp direct calls that exceed the input-schema maximum."""
        backend = AsyncMock()
        backend.exec_shell.return_value = ExecResult(0, b"", b"")
        tool = PowerShell(backend=backend)

        chunks = [
            chunk
            async for chunk in await tool(
                command="Get-Location",
                timeout=900000,
            )
        ]

        self.assertEqual(len(chunks), 1)
        self.assertEqual(
            backend.exec_shell.await_args.kwargs["timeout"],
            600.0,
        )


@unittest.skipUnless(
    sys.platform == "win32",
    "Windows PowerShell is only guaranteed on Windows",
)
class PowerShellWindowsIntegrationTest(IsolatedAsyncioTestCase):
    """Smoke-test the real Windows PowerShell executable."""

    async def test_real_powershell_returns_unicode_output(self) -> None:
        """Execute a Unicode command through LocalBackend end to end."""
        tool = PowerShell()

        chunks = [
            chunk
            async for chunk in await tool(
                command="Write-Output '你好 AgentScope'",
            )
        ]

        self.assertEqual(chunks[0].state, "running")
        self.assertEqual(
            chunks[0].content[0].text.strip(),
            "你好 AgentScope",
        )


if __name__ == "__main__":
    unittest.main()
