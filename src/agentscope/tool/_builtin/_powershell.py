# -*- coding: utf-8 -*-
"""The PowerShell tool in agentscope."""

import base64
import os
from typing import AsyncGenerator, Any, List

from ._backend import BackendBase, LocalBackend, _normalize_newlines
from .._base import ToolBase, ToolMiddlewareBase
from .._response import ToolChunk
from ...message import TextBlock, ToolResultState
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
    PermissionRule,
)


_SHELL_CANDIDATES = ("pwsh", "powershell.exe")


class PowerShell(ToolBase):
    """Execute PowerShell commands through a workspace backend."""

    name: str = "PowerShell"
    """The tool name presented to the agent."""

    description: str = """Executes a PowerShell command and returns its output.

Each command starts in the configured working directory, but PowerShell
session state does not persist between commands. Commands run without
loading the user's PowerShell profile.

IMPORTANT: Avoid using this tool for filesystem operations when a dedicated
tool can accomplish the task. Prefer the dedicated tools because their calls
are easier for the user to review and authorize:

 - File search: Use Glob (NOT Get-ChildItem)
 - Content search: Use Grep (NOT Select-String)
 - Read files: Use Read (NOT Get-Content)
 - Edit files: Use Edit
 - Write files: Use Write (NOT Set-Content or Out-File)
 - Communication: Output text directly (NOT Write-Output)

# Instructions
 - Verify the parent directory before creating new directories or files.
 - Quote paths containing spaces, for example: Get-Item "path with spaces".
 - Prefer absolute paths and avoid Set-Location so the working directory is
   clear in every command.
 - You may specify an optional timeout in milliseconds (up to 600000ms /
   10 minutes). The default timeout is 120000ms (2 minutes).
 - Write a concise description of what the command does. Include more context
   for pipelines, uncommon parameters, or commands with side effects.
 - Run independent commands in parallel tool calls. Keep dependent commands
   together and use PowerShell-native error handling when later work depends
   on earlier work succeeding.
 - Prefer new git commits over amending existing commits. Avoid destructive
   git operations and never bypass hooks unless the user explicitly asks."""
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The PowerShell command to execute.",
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command does."
                ),
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Optional timeout in milliseconds "
                    "(default: 120000, max: 600000)"
                ),
                "default": 120000,
                "maximum": 600000,
                "minimum": 0,
            },
        },
        "required": ["command"],
    }

    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = False
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(
        self,
        cwd: str | os.PathLike[str] | None = None,
        middlewares: List[ToolMiddlewareBase] | None = None,
        backend: BackendBase | None = None,
    ) -> None:
        """Initialize the PowerShell tool.

        Args:
            cwd (`str | os.PathLike[str] | None`, optional):
                Working directory used when executing commands.
            middlewares (`List[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping command execution.
            backend (`BackendBase | None`, optional):
                Backend used for subprocess execution. Defaults to the
                host-local backend.
        """
        super().__init__(middlewares=middlewares)
        self._cwd = os.fspath(cwd) if cwd is not None else None
        self._backend = backend or LocalBackend()
        self._executable: str | None = None

    async def _resolve_executable(self) -> str:
        """Prefer PowerShell 6+ and cache the first available executable."""
        if self._executable is None:
            for candidate in _SHELL_CANDIDATES:
                probe = await self._backend.exec_shell(
                    [
                        candidate,
                        "-NoLogo",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "exit 0",
                    ],
                    timeout=10.0,
                )
                if probe.exit_code != 127:
                    self._executable = candidate
                    break
            else:
                self._executable = "powershell.exe"
        return self._executable

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Ask the user to confirm every PowerShell command.

        PowerShell-specific command validation is intentionally outside this
        implementation. Since no command is classified as safe, every
        invocation prompts the user. This is a regular ASK that allow rules
        and BYPASS mode may still override.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Execute PowerShell command",
            decision_reason="PowerShell command validation is not enabled",
        )

    async def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        """Return no automatic allow-rule suggestions.

        A broad rule would weaken the conservative permission boundary before
        PowerShell-specific command validation is available.
        """
        return []

    async def call(  # type: ignore[override] # pylint: disable=unused-argument
        self,
        command: str,
        description: str = "",
        timeout: int = 120000,
    ) -> AsyncGenerator[ToolChunk, None]:
        """Execute a PowerShell command through the configured backend.

        Args:
            command (`str`):
                PowerShell source text to execute.
            description (`str`, optional):
                Human-readable description of the command.
            timeout (`int`, optional):
                Timeout in milliseconds, capped at 600000.

        Yields:
            `ToolChunk`:
                A final chunk containing the command output.
        """
        timeout_ms = min(timeout, 600000)
        encoded_user_command = base64.b64encode(
            command.encode("utf-16-le"),
        ).decode("ascii")
        powershell_script = (
            "$ProgressPreference = "
            "[System.Management.Automation.ActionPreference]::"
            "SilentlyContinue\n"
            "$OutputEncoding = [Console]::OutputEncoding = "
            "[System.Text.UTF8Encoding]::new($false)\n"
            "$AgentScopeCommand = [System.Text.Encoding]::Unicode.GetString("
            "[System.Convert]::FromBase64String("
            f"'{encoded_user_command}'))\n"
            "& ([ScriptBlock]::Create($AgentScopeCommand))"
        )
        encoded_command = base64.b64encode(
            powershell_script.encode("utf-16-le"),
        ).decode("ascii")
        try:
            executable = await self._resolve_executable()
            result = await self._backend.exec_shell(
                [
                    executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    encoded_command,
                ],
                cwd=self._cwd,
                timeout=timeout_ms / 1000.0,
            )
        except Exception as exc:
            yield ToolChunk(
                content=[
                    TextBlock(
                        text=f"Command failed: {command}\nError: {exc}",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )
            return

        stdout = _normalize_newlines(
            result.stdout.decode("utf-8", errors="replace"),
        )
        stderr = _normalize_newlines(
            result.stderr.decode("utf-8", errors="replace"),
        )
        if result.exit_code == -1 and result.stderr == b"timed out":
            yield ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"Command timed out after {timeout_ms}ms: "
                            f"{command}"
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )
            return

        if not result.ok():
            error_result = f"Command failed: {command}\n"
            if stdout:
                error_result += f"\nStdout:\n{stdout}"
            if stderr:
                error_result += f"\nStderr:\n{stderr}"
            if len(error_result) > 30000:
                error_result = (
                    error_result[:30000] + "\n... (output truncated)"
                )
            yield ToolChunk(
                content=[TextBlock(text=error_result)],
                state=ToolResultState.ERROR,
                is_last=True,
            )
            return

        output = stdout
        if stderr:
            if output:
                output += "\n"
            output += stderr
        if len(output) > 30000:
            output = output[:30000] + "\n... (output truncated)"
        yield ToolChunk(
            content=[TextBlock(text=output)],
            state=ToolResultState.RUNNING,
            is_last=True,
        )
