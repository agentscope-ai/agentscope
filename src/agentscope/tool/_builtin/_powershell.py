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

    description: str = """执行 PowerShell 命令并返回其输出。

每条命令都在配置的工作目录中启动，但 PowerShell 会话状态不会在命令之间
保留。命令执行时不加载用户的 PowerShell 配置文件。

重要提示：当专用工具可以完成文件系统操作时，避免使用本工具。请优先使用
专用工具，因为它们的调用更容易让用户审查和授权：

 - 文件搜索：使用 Glob（而不是 Get-ChildItem）
 - 内容搜索：使用 Grep（而不是 Select-String）
 - 读取文件：使用 Read（而不是 Get-Content）
 - 编辑文件：使用 Edit
 - 写入文件：使用 Write（而不是 Set-Content 或 Out-File）
 - 通信：直接输出文本（而不是 Write-Output）

# 说明
 - 在创建新目录或文件之前，先确认父目录是否存在。
 - 用引号引用包含空格的路径，例如：Get-Item "path with spaces"。
 - 优先使用绝对路径并避免使用 Set-Location，这样每条命令的工作目录都清晰
   明了。
 - 你可以指定可选的超时时间（毫秒，最大 600000ms / 10 分钟）。默认超时
   时间为 120000ms（2 分钟）。
 - 为命令编写简洁的描述。对于管道、不常见的参数或具有副作用的命令，请提供
   更多上下文。
 - 在并行的工具调用中运行相互独立的命令。让相互依赖的命令保持在一起，并且
   当后续工作依赖前面的工作成功完成时，使用 PowerShell 原生的错误处理。
 - 优先创建新的 git 提交，而不是修改已有提交。避免破坏性的 git 操作，除非
   用户明确要求，否则绝不绕过钩子。"""
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 PowerShell 命令。",
            },
            "description": {
                "type": "string",
                "description": (
                    "对此命令作用的清晰、简洁的描述。"
                ),
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "可选的超时时间（毫秒）"
                    "（默认值：120000，最大值：600000）"
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
