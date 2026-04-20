# -*- coding: utf-8 -*-
"""The read tool in agentscope."""
import os
from typing import Any

import aiofiles

from .._base import ToolBase
from .._permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .._response import ToolResponse
from ...message import TextBlock


class Read(ToolBase):
    """The read tool."""

    name: str = "read"
    """The tool name presented to the agent."""

    # pylint: disable=line-too-long
    description: str = """Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The file_path parameter must be an absolute path, not a relative path
- By default, it reads up to 2000 lines starting from the beginning of the file
- You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters
- Results are returned using cat -n format, with line numbers starting at 1
- This tool allows Claude Code to read images (eg PNG, JPG, etc). When reading an image file the contents are presented visually as Claude Code is a multimodal LLM.
- This tool can read PDF files (.pdf). For large PDFs (more than 10 pages), you MUST provide the pages parameter to read specific pages."""  # noqa: E501
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": "Optional 1-based line number to start reading "
                "from (default: 1)",
                "default": 1,
                "minimum": 1,
            },
            "limit": {
                "type": "integer",
                "description": "Optional maximum number of lines to read "
                "(default: 2000, max: 2000)",
                "default": 2000,
                "maximum": 2000,
                "minimum": 1,
            },
        },
        "required": ["file_path"],
    }

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True

    def __init__(self) -> None:
        """Initialize the read tool."""

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for file reading."""
        # Read is read-only, always allow
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="File reading is read-only.",
        )

    async def __call__(  # type: ignore[override]
        self,
        file_path: str,
        offset: int = 1,
        limit: int = 2000,
    ) -> ToolResponse:
        """Read the file and return the content with line numbers."""

        # Validate file_path is absolute
        if not os.path.isabs(file_path):
            return ToolResponse(
                content=[
                    TextBlock(
                        text=f"Error: file_path must be an absolute path, "
                        f"got: {file_path}",
                    ),
                ],
                state="error",
            )

        # Check file exists
        if not os.path.exists(file_path):
            return ToolResponse(
                content=[
                    TextBlock(text=f"Error: File does not exist: {file_path}"),
                ],
                state="error",
            )

        # Check it's not a directory
        if os.path.isdir(file_path):
            return ToolResponse(
                content=[
                    TextBlock(
                        text=f"Error: Path is a directory, not a file: "
                        f"{file_path}",
                    ),
                ],
                state="error",
            )

        try:
            # Read file content with aiofiles
            async with aiofiles.open(
                file_path,
                mode="r",
                encoding="utf-8",
                errors="replace",
            ) as f:
                lines = await f.readlines()

            # Apply offset and limit (offset is 1-based)
            start_idx = offset - 1
            end_idx = start_idx + limit
            selected_lines = lines[start_idx:end_idx]

            # Format with line numbers (6-char padded + tab + content)
            formatted_lines = []
            for i, line in enumerate(selected_lines, start=offset):
                # Remove trailing newline if present
                line_content = line.rstrip("\n\r")

                # Truncate lines longer than 2000 chars
                if len(line_content) > 2000:
                    line_content = line_content[:2000] + "[truncated]"

                # Format: 6-char padded line number + tab + content
                formatted_line = f"{i:6d}\t{line_content}"
                formatted_lines.append(formatted_line)

            # Join all lines
            result = "\n".join(formatted_lines)

            return ToolResponse(
                content=[TextBlock(text=result)],
                state="finished",
            )

        except Exception as e:
            return ToolResponse(
                content=[TextBlock(text=f"Error reading file: {str(e)}")],
                state="error",
            )
