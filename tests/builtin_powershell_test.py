# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""PowerShell tool test cases."""

import base64
import sys
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.permission import PermissionBehavior, PermissionContext
from agentscope.tool import ExecResult, LocalBackend, PowerShell


class PowerShellInterfaceTest(IsolatedAsyncioTestCase):
    """Test the public PowerShell interface and permission hooks."""

    async def test_public_interface_and_read_only_allow(self) -> None:
        """Expose the command schema and auto-allow read-only cmdlets."""
        backend = LocalBackend()
        tool = PowerShell(cwd="workspace", backend=backend)

        self.assertEqual(tool.name, "PowerShell")
        self.assertFalse(tool.is_read_only)
        self.assertFalse(tool.is_concurrency_safe)
        self.assertIn("command", tool.input_schema["properties"])
        self.assertEqual(tool.input_schema["required"], ["command"])
        self.assertEqual(tool._cwd, "workspace")
        self.assertIs(tool._backend, backend)
        self.assertIn("Glob", tool.description)
        self.assertIn("Read", tool.description)
        self.assertIn("Write", tool.description)
        self.assertIn("600000", tool.description)

        decision = await tool.check_permissions(
            {"command": "Get-Location"},
            PermissionContext(),
        )
        self.assertEqual(
            {
                "behavior": decision.behavior,
                "bypass_immune": decision.bypass_immune,
                "decision_reason": decision.decision_reason,
            },
            {
                "behavior": PermissionBehavior.ALLOW,
                "bypass_immune": False,
                "decision_reason": "Read-only command is allowed",
            },
        )
        self.assertTrue(
            await tool.check_read_only({"command": "Get-ChildItem"}),
        )
        self.assertTrue(await tool.check_read_only({"command": "ls"}))
        self.assertFalse(
            await tool.check_read_only({"command": "Remove-Item ./x"}),
        )

        suggestions = await tool.generate_suggestions(
            {"command": "Remove-Item ./tmp"},
        )
        self.assertEqual(
            [
                {
                    "tool_name": rule.tool_name,
                    "rule_content": rule.rule_content,
                    "behavior": rule.behavior,
                    "source": rule.source,
                }
                for rule in suggestions
            ],
            [
                {
                    "tool_name": "PowerShell",
                    "rule_content": "Remove-Item:*",
                    "behavior": PermissionBehavior.ALLOW,
                    "source": "suggested",
                },
            ],
        )


class PowerShellPermissionTest(IsolatedAsyncioTestCase):
    """Test PowerShell permission tiers and rule matching."""

    async def asyncSetUp(self) -> None:
        """Create a PowerShell tool for permission tests."""
        self.tool = PowerShell(backend=LocalBackend())

    async def test_dangerous_commands_are_bypass_immune_ask(self) -> None:
        """Dangerous patterns return bypass-immune ASK decisions."""
        decision = await self.tool.check_permissions(
            {"command": "Remove-Item -Recurse -Force ./tmp"},
            PermissionContext(),
        )
        self.assertEqual(
            {
                "behavior": decision.behavior,
                "bypass_immune": decision.bypass_immune,
            },
            {
                "behavior": PermissionBehavior.ASK,
                "bypass_immune": True,
            },
        )
        self.assertIn("Remove-Item", decision.message)

    async def test_injection_is_bypass_immune_ask(self) -> None:
        """Injection risk returns a bypass-immune ASK."""
        decision = await self.tool.check_permissions(
            {"command": "iex '1'"},
            PermissionContext(),
        )
        self.assertEqual(
            {
                "behavior": decision.behavior,
                "bypass_immune": decision.bypass_immune,
            },
            {
                "behavior": PermissionBehavior.ASK,
                "bypass_immune": True,
            },
        )

    async def test_mutating_non_dangerous_is_passthrough(self) -> None:
        """Ordinary mutating commands fall through to the engine."""
        decision = await self.tool.check_permissions(
            {"command": "New-Item -ItemType File -Path a.txt"},
            PermissionContext(),
        )
        self.assertEqual(
            decision.behavior,
            PermissionBehavior.PASSTHROUGH,
        )

    async def test_match_rule_wildcard_and_alias(self) -> None:
        """Content rules match with wildcards, case, and aliases."""
        self.assertTrue(
            await self.tool.match_rule(
                "Remove-Item*",
                {"command": "Remove-Item ./tmp"},
            ),
        )
        self.assertTrue(
            await self.tool.match_rule(
                "Get-ChildItem*",
                {"command": "ls"},
            ),
        )
        self.assertTrue(
            await self.tool.match_rule(
                "get-childitem:*",
                {"command": "Get-ChildItem -Path ."},
            ),
        )
        self.assertTrue(
            await self.tool.match_rule(
                None,
                {"command": "anything"},
            ),
        )
        self.assertFalse(
            await self.tool.match_rule(
                "Remove-Item*",
                {"command": "Get-ChildItem"},
            ),
        )


class PowerShellExecutionTest(IsolatedAsyncioTestCase):
    """Test PowerShell command execution through the backend."""

    async def test_call_uses_encoded_command_and_cwd(self) -> None:
        """Encode commands for PowerShell and pass cwd to the backend."""
        backend = AsyncMock()
        backend.exec_shell.side_effect = [
            ExecResult(0, b"", b""),
            ExecResult(0, b"ok\r\n", b""),
        ]
        tool = PowerShell(cwd="workspace", backend=backend)

        chunks = [
            chunk
            async for chunk in await tool(
                command="Get-Location",
            )
        ]

        argv = backend.exec_shell.await_args_list[1].args[0]
        self.assertEqual(
            argv[:-1],
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
            ],
        )
        decoded_command = base64.b64decode(argv[-1]).decode("utf-16-le")
        encoded_user_command = base64.b64encode(
            "Get-Location".encode("utf-16-le"),
        ).decode("ascii")
        self.assertIn("[Console]::OutputEncoding", decoded_command)
        self.assertIn(
            f"FromBase64String('{encoded_user_command}')",
            decoded_command,
        )
        self.assertIn("[ScriptBlock]::Create", decoded_command)
        self.assertEqual(
            backend.exec_shell.await_args_list[1].kwargs,
            {"cwd": "workspace", "timeout": 120.0},
        )
        self.assertEqual(chunks[0].content[0].text, "ok\n")
        self.assertEqual(chunks[0].state, "running")

    async def test_prefers_pwsh_and_caches_resolution(self) -> None:
        """Probe modern PowerShell once and reuse it for later calls."""
        backend = AsyncMock()
        backend.exec_shell.side_effect = [
            ExecResult(0, b"", b""),
            ExecResult(0, b"first", b""),
            ExecResult(0, b"second", b""),
        ]
        tool = PowerShell(backend=backend)

        first = [chunk async for chunk in await tool(command="'first'")]
        second = [chunk async for chunk in await tool(command="'second'")]

        self.assertEqual(first[0].content[0].text, "first")
        self.assertEqual(second[0].content[0].text, "second")
        self.assertEqual(backend.exec_shell.await_count, 3)
        self.assertEqual(
            [call.args[0][0] for call in backend.exec_shell.await_args_list],
            ["pwsh", "pwsh", "pwsh"],
        )

    async def test_falls_back_to_windows_powershell(self) -> None:
        """Use powershell.exe when pwsh is unavailable."""
        backend = AsyncMock()
        backend.exec_shell.side_effect = [
            ExecResult(127, b"", b"not found"),
            ExecResult(0, b"", b""),
            ExecResult(0, b"legacy", b""),
        ]
        tool = PowerShell(backend=backend)

        chunks = [chunk async for chunk in await tool(command="'legacy'")]

        self.assertEqual(chunks[0].content[0].text, "legacy")
        self.assertEqual(
            [call.args[0][0] for call in backend.exec_shell.await_args_list],
            ["pwsh", "powershell.exe", "powershell.exe"],
        )

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

    async def test_real_powershell_handles_script_preamble(self) -> None:
        """Preserve param blocks, comments, and quoted arguments."""
        tool = PowerShell()

        chunks = [
            chunk
            async for chunk in await tool(
                command=(
                    "param()\n"
                    "# A leading script comment must not swallow the command\n"
                    'Write-Output "a b"'
                ),
            )
        ]

        self.assertEqual(chunks[0].state, "running")
        self.assertEqual(chunks[0].content[0].text.strip(), "a b")

    async def test_real_powershell_preserves_user_line_numbers(self) -> None:
        """Report line numbers relative to the user's command text."""
        tool = PowerShell()

        chunks = [
            chunk
            async for chunk in await tool(
                command=(
                    "try {\n"
                    "    throw 'boom'\n"
                    "} catch {\n"
                    "    Write-Output $_.InvocationInfo.ScriptLineNumber\n"
                    "}"
                ),
            )
        ]

        self.assertEqual(chunks[0].state, "running")
        self.assertEqual(chunks[0].content[0].text.strip(), "2")


if __name__ == "__main__":
    unittest.main()
