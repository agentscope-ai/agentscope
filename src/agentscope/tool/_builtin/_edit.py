# -*- coding: utf-8 -*-
"""The edit tool in agentscope."""
import os
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


class Edit(ToolBase):
    """The edit tool for performing exact string replacements in files."""

    name: str = "edit"
    """The tool name presented to the agent."""

    description: str = """Performs exact string replacements in files.

Usage:
- You must use your `Read` tool at least once in the conversation
  before editing. This tool will error if you attempt an edit without
  reading the file.
- When editing text from Read tool output, ensure you preserve the
  exact indentation (tabs/spaces) as it appears AFTER the line number
  prefix. The line number prefix format is: line number + tab.
  Everything after that is the actual file content to match. Never
  include any part of the line number prefix in the old_string or
  new_string.
- ALWAYS prefer editing existing files in the codebase. NEVER write
  new files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding
  emojis to files unless asked.
- The edit will FAIL if `old_string` is not unique in the file."""  # noqa: E501
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to edit.",
            },
            "old_string": {
                "type": "string",
                "description": (
                    "The exact string to replace. Must match exactly "
                    "including whitespace and indentation."
                ),
            },
            "new_string": {
                "type": "string",
                "description": "The string to replace old_string with.",
            },
            "replace_all": {
                "type": "boolean",
                "description": (
                    "If true, replace all occurrences. If false "
                    "(default), only replace if there is exactly one "
                    "occurrence."
                ),
                "default": False,
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = False

    def __init__(self) -> None:
        """Initialize the edit tool."""

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for file editing."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Edit file: {tool_input.get('file_path', '')}",
        )

    async def __call__(  # type: ignore[override]
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> AsyncGenerator[ToolChunk, None]:
        """Execute the edit and return the result."""

        # Validate file_path is absolute
        if not os.path.isabs(file_path):
            yield ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"Error: file_path must be an absolute "
                            f"path, got: {file_path}"
                        ),
                    ),
                ],
                state="error",
                is_last=True,
            )
            return

        # Check file exists
        if not os.path.exists(file_path):
            yield ToolChunk(
                content=[
                    TextBlock(text=f"Error: File not found: {file_path}"),
                ],
                state="error",
                is_last=True,
            )
            return

        # Check old_string != new_string
        if old_string == new_string:
            yield ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            "Error: old_string and new_string are "
                            "identical. No changes to make."
                        ),
                    ),
                ],
                state="error",
                is_last=True,
            )
            return

        # Read file content
        try:
            async with aiofiles.open(
                file_path,
                "r",
                encoding="utf-8",
            ) as f:
                content = await f.read()
        except Exception as e:
            yield ToolChunk(
                content=[TextBlock(text=f"Error reading file: {str(e)}")],
                state="error",
                is_last=True,
            )
            return

        # Count occurrences
        occurrences = content.count(old_string)

        # If occurrences == 0, raise error
        if occurrences == 0:
            yield ToolChunk(
                content=[
                    TextBlock(
                        text=f"Error: old_string not found in {file_path}",
                    ),
                ],
                state="error",
                is_last=True,
            )
            return

        # If occurrences > 1 and not replace_all, raise error
        if occurrences > 1 and not replace_all:
            yield ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"Error: old_string appears {occurrences} "
                            f"times in {file_path}. Set replace_all=true "
                            f"to replace all occurrences, or make "
                            f"old_string more specific."
                        ),
                    ),
                ],
                state="error",
                is_last=True,
            )
            return

        # Perform replacement
        if replace_all:
            updated_content = content.replace(old_string, new_string)
        else:
            updated_content = content.replace(
                old_string,
                new_string,
                1,
            )

        # Write updated content back to file
        try:
            async with aiofiles.open(
                file_path,
                "w",
                encoding="utf-8",
            ) as f:
                await f.write(updated_content)
        except Exception as e:
            yield ToolChunk(
                content=[TextBlock(text=f"Error writing file: {str(e)}")],
                state="error",
                is_last=True,
            )
            return

        # Return success message
        replacement_msg = (
            f"all {occurrences} occurrences" if replace_all else "1 occurrence"
        )
        yield ToolChunk(
            content=[
                TextBlock(
                    text=f"Successfully replaced {replacement_msg} "
                    f"in {file_path}",
                ),
            ],
            state="running",
            is_last=True,
        )
