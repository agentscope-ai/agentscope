# -*- coding: utf-8 -*-
"""The python tool in agentscope."""
import os
from typing import AsyncGenerator, Any, List

from .._base import ToolBase, ToolMiddlewareBase
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from ...message import TextBlock, ToolResultState
from .._response import ToolChunk
from ._backend import BackendBase


class Python(ToolBase):
    """The python execution tool."""

    name: str = "Python"
    """The tool name presented to the agent."""

    description: str = """Executes Python code and returns its output.
    
Usage:
- Provide valid Python code in the `code` parameter.
- The code will be saved to a temporary file and executed via `python`.
- Standard output and standard error will be returned.
- Use `print()` to output results you want to see.
"""
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The Python code to execute.",
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
        "required": ["code"],
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
        """Initialize the python tool.

        Args:
            cwd (`str | os.PathLike[str] | None`, optional):
                The working directory used when executing code.
            middlewares (`List[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
            backend (`BackendBase | None`, optional):
                The sandbox backend to use for shell execution. When
                ``None``, a :class:`LocalBackend` is created.
        """
        from ._backend import LocalBackend

        super().__init__(middlewares=middlewares)
        self._cwd = os.fspath(cwd) if cwd is not None else None
        self._backend = backend or LocalBackend()

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for python execution."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Permission required to execute Python code.",
            decision_reason="Safety check: executing arbitrary python code.",
            bypass_immune=True,
        )

    async def call(  # type: ignore[override]
        self,
        code: str,
        timeout: int = 120000,
    ) -> AsyncGenerator[ToolChunk, None]:
        """Execute the python code and return the output.

        Args:
            code: The python code to execute.
            timeout: Timeout in milliseconds (default: 120000, max: 600000).

        Yields:
            ToolChunk: The tool execution result with stdout/stderr content.
        """
        import tempfile

        timeout_ms = min(timeout, 600000)
        timeout_sec = timeout_ms / 1000.0

        try:
            # Create a temporary file to hold the code
            fd, path = tempfile.mkstemp(suffix=".py", text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(code)

            command = ["python", path]
            result = await self._backend.exec_shell(
                command,
                cwd=self._cwd,
                timeout=timeout_sec,
            )

            # Clean up the temporary file
            try:
                os.remove(path)
            except OSError:
                pass

            # Decode and normalize line endings
            stdout = result.stdout.decode(
                "utf-8",
                errors="replace",
            ).replace("\r\n", "\n")
            stderr = result.stderr.decode(
                "utf-8",
                errors="replace",
            ).replace("\r\n", "\n")

            if result.exit_code == -1 and result.stderr == b"timed out":
                error_msg = f"Code execution timed out after {timeout_ms}ms"
                yield ToolChunk(
                    content=[TextBlock(text=error_msg)],
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

            if not result.ok():
                error_result = "Execution failed\n"
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
            else:
                yield ToolChunk(
                    content=[TextBlock(text=output)],
                    state=ToolResultState.RUNNING,
                    is_last=True,
                )

        except Exception as e:
            error_msg = f"Execution failed: {str(e)}"
            yield ToolChunk(
                content=[TextBlock(text=error_msg)],
                state=ToolResultState.ERROR,
                is_last=True,
            )
