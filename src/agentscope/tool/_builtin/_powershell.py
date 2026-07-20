# -*- coding: utf-8 -*-
"""The PowerShell tool in agentscope."""

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


class PowerShell(ToolBase):
    """Execute PowerShell commands through a workspace backend."""

    name: str = "PowerShell"
    """The tool name presented to the agent."""

    description: str = (
        "Executes a PowerShell command and returns its output. "
        "Commands run without loading the user's PowerShell profile."
    )
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

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Defer every command to the configured permission mode.

        PowerShell-specific command validation is intentionally outside this
        implementation. No command is automatically classified as safe.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
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
        utf8_command = (
            "$OutputEncoding = [Console]::OutputEncoding = "
            "[System.Text.UTF8Encoding]::new($false); "
            f"{command}"
        )
        try:
            result = await self._backend.exec_shell(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    utf8_command,
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
