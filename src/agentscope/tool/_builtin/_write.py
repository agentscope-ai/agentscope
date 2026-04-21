# -*- coding: utf-8 -*-
"""The write tool in agentscope."""
import os
from pathlib import Path
from typing import Any, TYPE_CHECKING

import aiofiles

from .._base import ToolBase
from .._permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionMode,
)
from .._response import ToolChunk
from ...message import TextBlock

if TYPE_CHECKING:
    from ...agent import AgentState
else:
    AgentState = Any


class Write(ToolBase):
    """The write tool."""

    name: str = "Write"
    """The tool name presented to the agent."""

    # pylint: disable=line-too-long
    description: str = """Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked."""  # noqa: E501
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to write "
                "(must be absolute, not relative)",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file",
            },
        },
        "required": ["file_path", "content"],
    }

    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = False

    def __init__(
        self,
        additional_dangerous_files: list[str] | None = None,
        additional_dangerous_directories: list[str] | None = None,
    ) -> None:
        """Initialize the write tool.

        Args:
            additional_dangerous_files (`list[str] | None`, optional):
                Additional dangerous files to check (added to built-in
                defaults). Use this to add project-specific sensitive files
                like '.env' or '.secrets'.
            additional_dangerous_directories (`list[str] | None`, optional):
                Additional dangerous directories to check (added to built-in
                defaults). Use this to add project-specific sensitive
                directories.
        """
        # Merge class-level dangerous paths with additional ones
        self.dangerous_files = self.__class__.dangerous_files.copy()
        if additional_dangerous_files:
            self.dangerous_files.extend(additional_dangerous_files)

        self.dangerous_directories = (
            self.__class__.dangerous_directories.copy()
        )
        if additional_dangerous_directories:
            self.dangerous_directories.extend(additional_dangerous_directories)

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for file writing.

        This method implements Write-specific permission checks:
        1. Dangerous path check (safety check, bypass-immune)
        2. ACCEPT_EDITS mode check for files in working directories

        Args:
            tool_input (`dict[str, Any]`):
                The tool input containing "file_path" key
            context (`PermissionContext`):
                The permission context with mode and rules

        Returns:
            `PermissionDecision`:
                ASK for dangerous paths, ALLOW for safe operations in
                ACCEPT_EDITS mode, PASSTHROUGH otherwise
        """

        file_path = tool_input.get("file_path")
        if not file_path:
            return PermissionDecision(
                behavior=PermissionBehavior.PASSTHROUGH,
                message="No file path provided",
            )

        # 1. Check for dangerous paths (safety check, bypass-immune)
        if self._is_dangerous_path(file_path):
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Permission required: Write operation on "
                f"sensitive file {file_path}",
                decision_reason="Safety check: dangerous file or directory",
            )

        # 2. Check ACCEPT_EDITS mode for files in working directories
        if context.mode == PermissionMode.ACCEPT_EDITS:
            if self._path_in_allowed_working_path(file_path, context):
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=f"Permission granted for writing {file_path} "
                    f"(accept edits mode - in working directory)",
                    decision_reason="File is in working directory and not "
                    "a dangerous path",
                )

        # 3. Default to asking for permission
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Claude requested permissions to write to {file_path}, "
            f"but you haven't granted it yet.",
        )

    def _path_in_allowed_working_path(
        self,
        file_path: str,
        context: PermissionContext,
    ) -> bool:
        """Check if a file path is within any allowed working directory.

        Args:
            file_path (`str`):
                The file path to check
            context (`PermissionContext`):
                The permission context containing working directories

        Returns:
            `bool`:
                True if the path is within any allowed working directory
        """

        # Get all working directories (current directory + additional)
        current_dir = os.getcwd()
        additional_dirs = list(context.working_directories.keys())
        all_working_dirs = [current_dir] + additional_dirs

        # Normalize paths
        abs_file_path = os.path.abspath(os.path.expanduser(file_path))

        # Check if file path is in any working directory
        for working_dir in all_working_dirs:
            abs_working_dir = os.path.abspath(os.path.expanduser(working_dir))
            try:
                # Check if file_path is inside working_dir
                os.path.relpath(abs_file_path, abs_working_dir)
                if (
                    abs_file_path.startswith(abs_working_dir + os.sep)
                    or abs_file_path == abs_working_dir
                ):
                    return True
            except ValueError:
                # On Windows, relpath raises ValueError if paths are on
                # different drives
                continue

        return False

    async def __call__(  # type: ignore[override]
        self,
        file_path: str,
        content: str,
        _agent_state: AgentState | None = None,
    ) -> ToolChunk:
        """Write content to a file and return the result."""
        # Validate that file_path is absolute
        if not os.path.isabs(file_path):
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Error: file_path must be an absolute path, "
                        f"got: {file_path}",
                    ),
                ],
                state="error",
                is_last=True,
            )

        # Check if file exists, it must be read first if it exists
        if os.path.exists(file_path) and _agent_state is not None:
            cache = await _agent_state.tool_context.get_cache(file_path)
            if cache is None:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=f"Error: File {file_path} exists but has not "
                            f"been read yet. You must read the file first "
                            f"before writing to it.",
                        ),
                    ],
                    state="error",
                    is_last=True,
                )

        # Create parent directories if they don't exist
        parent_dir = Path(file_path).parent
        os.makedirs(parent_dir, exist_ok=True)

        # Write content to file
        async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
            await f.write(content)

        # Count lines in content
        line_count = len(content.split("\n"))

        # Return success message
        return ToolChunk(
            content=[
                TextBlock(
                    text=f"The file {file_path} has been written successfully "
                    f"({line_count} lines).",
                ),
            ],
            state="running",
            is_last=True,
        )
