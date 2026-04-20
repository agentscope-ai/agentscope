# -*- coding: utf-8 -*-
"""The write tool in agentscope."""
import os
from pathlib import Path
from typing import AsyncGenerator, Any

import aiofiles

from .._base import ToolBase
from .._permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .._response import ToolChunk
from ...message import TextBlock


class Write(ToolBase):
    """The write tool."""

    name: str = "write"
    """The tool name presented to the agent."""

    description: str = """Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked."""
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to write (must be absolute, not relative)",
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

    def __init__(self) -> None:
        """Initialize the write tool."""

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for file writing."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Write file: {tool_input.get('file_path', '')}",
        )

    async def __call__(  # type: ignore[override]
        self,
        file_path: str,
        content: str,
    ) -> AsyncGenerator[ToolChunk, None]:
        """Write content to a file and return the result."""
        # Validate that file_path is absolute
        if not os.path.isabs(file_path):
            yield ToolChunk(
                content=[
                    TextBlock(
                        text=f"Error: file_path must be an absolute path, got: {file_path}",
                    ),
                ],
                state="error",
                is_last=True,
            )
            return

        # Create parent directories if they don't exist
        parent_dir = Path(file_path).parent
        os.makedirs(parent_dir, exist_ok=True)

        # Write content to file
        async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
            await f.write(content)

        # Count lines in content
        line_count = len(content.split("\n"))

        # Return success message
        yield ToolChunk(
            content=[
                TextBlock(
                    text=f"The file {file_path} has been written successfully ({line_count} lines).",
                ),
            ],
            state="running",
            is_last=True,
        )
