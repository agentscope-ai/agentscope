# -*- coding: utf-8 -*-
"""The bash tool in agentscope."""
from typing import AsyncGenerator, Any

from .._base import ToolBase
from .._permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .._response import ToolChunk


class Bash(ToolBase):
    """The bash tool."""

    name: str = "bash"
    """The tool name presented to the agent."""

    description: str = """Executes a bash command and returns its output.

The working directory persists between commands, but shell state does
not. The shell environment is initialized from the user's profile
(bash or zsh).

IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`,
`tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed
or after you have verified that a dedicated tool cannot accomplish your
task. Instead, use the appropriate dedicated tool as this will provide
a much better experience for the user:

 - File search: Use Glob (NOT find or ls)
 - Content search: Use Grep (NOT grep or rg)
 - Read files: Use Read (NOT cat/head/tail)
 - Edit files: Use Edit (NOT sed/awk)
 - Write files: Use Write (NOT echo >/cat <<EOF)
 - Communication: Output text directly (NOT echo/printf)

While the Bash tool can do similar things, it's better to use the
built-in tools as they provide a better user experience and make it
easier to review tool calls and give permission.

# Instructions
 - If your command will create new directories or files, first use
   this tool to run `ls` to verify the parent directory exists and is
   the correct location.
 - Always quote file paths that contain spaces with double quotes in
   your command (e.g., cd "path with spaces/file.txt")
 - Try to maintain your current working directory throughout the
   session by using absolute paths and avoiding usage of `cd`. You may
   use `cd` if the User explicitly requests it.
 - You may specify an optional timeout in milliseconds (up to 600000ms
   / 10 minutes). By default, your command will timeout after 120000ms
   (2 minutes).
 - Write a clear, concise description of what your command does. For
   simple commands, keep it brief (5-10 words). For complex commands
   (piped commands, obscure flags, or anything hard to understand at a
   glance), include enough context so that the user can understand what
   your command will do.
 - When issuing multiple commands:
  - If the commands are independent and can run in parallel, make
    multiple Bash tool calls in a single message. Example: if you need
    to run "git status" and "git diff", send a single message with two
    Bash tool calls in parallel.
  - If the commands depend on each other and must run sequentially,
    use a single Bash call with '&&' to chain them together.
  - Use ';' only when you need to run commands sequentially but don't
    care if earlier commands fail.
  - DO NOT use newlines to separate commands (newlines are ok in
    quoted strings).
 - For git commands:
  - Prefer to create a new commit rather than amending an existing
    commit.
  - Before running destructive operations (e.g., git reset --hard, git
    push --force, git checkout --), consider whether there is a safer
    alternative that achieves the same goal. Only use destructive
    operations when they are truly the best approach.
  - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign,
    -c commit.gpgsign=false) unless the user has explicitly asked for
    it. If a hook fails, investigate and fix the underlying issue.
 - Avoid unnecessary `sleep` commands:
  - Do not sleep between commands that can run immediately — just run
    them.
  - Do not retry failing commands in a sleep loop — diagnose the root
    cause or consider an alternative approach.
  - If you must sleep, keep the duration short (1-5 seconds) to avoid
    blocking the user."""
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute.",
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command "
                    "does. For simple commands, keep it brief (5-10 "
                    "words). For complex commands, include enough "
                    "context."
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

    def __init__(self) -> None:
        """Initialize the bash tool."""

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for bash command execution."""
        # Bash commands require permission checking
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Execute bash command: {tool_input.get('command', '')}",
        )

    async def __call__(  # type: ignore[override]
        self,
        command: str,
        description: str = "",
        timeout: int = 120000,
    ) -> AsyncGenerator[ToolChunk, None]:
        """Execute the bash and return the output.

        Args:
            command: The bash command to execute.
            description: Optional description of what the command does.
            timeout: Timeout in milliseconds (default: 120000, max: 600000).

        Yields:
            ToolChunk: The tool execution result with stdout/stderr content.
        """
        import asyncio
        from ...message import TextBlock

        # Clamp timeout to max 600000ms and convert to seconds
        timeout_ms = min(timeout, 600000)
        timeout_sec = timeout_ms / 1000.0

        try:
            # Create subprocess
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Wait for completion with timeout
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_sec,
            )

            # Decode and normalize line endings
            stdout = stdout_bytes.decode("utf-8", errors="replace").replace(
                "\r\n",
                "\n",
            )
            stderr = stderr_bytes.decode("utf-8", errors="replace").replace(
                "\r\n",
                "\n",
            )

            # Combine output
            output = stdout
            if stderr:
                if output:
                    output += "\n"
                output += stderr

            # Truncate if exceeds 30000 characters
            if len(output) > 30000:
                output = output[:30000] + "\n... (output truncated)"

            # Check exit code
            if process.returncode != 0:
                # Command failed
                result = f"Command failed: {command}\n"
                if stdout:
                    result += f"\nStdout:\n{stdout}"
                if stderr:
                    result += f"\nStderr:\n{stderr}"

                # Truncate error message if needed
                if len(result) > 30000:
                    result = result[:30000] + "\n... (output truncated)"

                yield ToolChunk(
                    content=[TextBlock(text=result)],
                    state="error",
                    is_last=True,
                )
            else:
                # Command succeeded - note: ToolChunk uses "running" state
                # which will be converted to "finished" in ToolResponse
                yield ToolChunk(
                    content=[TextBlock(text=output)],
                    state="running",
                    is_last=True,
                )

        except asyncio.TimeoutError:
            # Timeout occurred
            error_msg = f"Command timed out after {timeout_ms}ms: {command}"
            yield ToolChunk(
                content=[TextBlock(text=error_msg)],
                state="error",
                is_last=True,
            )

        except Exception as e:
            # Other errors
            error_msg = f"Command failed: {command}\nError: {str(e)}"
            yield ToolChunk(
                content=[TextBlock(text=error_msg)],
                state="error",
                is_last=True,
            )
