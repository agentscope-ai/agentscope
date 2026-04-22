# -*- coding: utf-8 -*-
"""The edit tool in agentscope."""
import fnmatch
import os
from typing import Any, List, TYPE_CHECKING

import aiofiles

from .._base import ToolBase
from .._permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
)
from .._response import ToolChunk
from ...message import TextBlock

if TYPE_CHECKING:
    from ...agent import AgentState
else:
    AgentState = Any


class Edit(ToolBase):
    """The edit tool for performing exact string replacements in files."""

    name: str = "Edit"
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

    def __init__(
        self,
        additional_dangerous_files: list[str] | None = None,
        additional_dangerous_directories: list[str] | None = None,
    ) -> None:
        """Initialize the edit tool.

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
        """Check permissions for file editing.

        This method implements Edit-specific permission checks:
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
                message=f"Permission required: Edit operation on "
                f"sensitive file {file_path}",
                decision_reason="Safety check: dangerous file or directory",
            )

        # 2. Check ACCEPT_EDITS mode for files in working directories
        if context.mode == PermissionMode.ACCEPT_EDITS:
            if self._path_in_allowed_working_path(file_path, context):
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message=f"Permission granted for editing {file_path} "
                    f"(accept edits mode - in working directory)",
                    decision_reason="File is in working directory and not "
                    "a dangerous path",
                )

        # 3. Default to asking for permission
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Request permissions to write to {file_path}, "
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

    def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        """Check if a permission rule matches the file path.

        Matches rule_content as a glob pattern against the "file_path"
        parameter using fnmatch. If rule_content is None, matches all
        invocations (tool-name-level rule).

        Args:
            rule_content (`str | None`):
                Glob pattern to match against the file path (e.g., "src/**"),
                or None to match all invocations
            tool_input (`dict[str, Any]`):
                The tool input data containing "file_path" key

        Returns:
            `bool`:
                True if the glob pattern matches the file path, False otherwise
        """
        if rule_content is None:
            return True

        file_path = tool_input.get("file_path", "")
        if not file_path:
            return False
        return fnmatch.fnmatch(file_path, rule_content)

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        """Generate suggested permission rules for the file path.

        Suggests a glob pattern covering the parent directory of the file,
        allowing the user to grant permission for the entire directory at once.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data containing "file_path" key

        Returns:
            `List[PermissionRule]`:
                A single suggested rule covering the parent directory
                (e.g., file "/src/main.py" -> rule "src/**")
        """
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return []

        parent = os.path.dirname(file_path)
        pattern = (parent.rstrip("/") + "/**") if parent else "**"

        return [
            PermissionRule(
                tool_name=self.name,
                rule_content=pattern,
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]

    async def __call__(  # type: ignore[override]
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        _agent_state: AgentState | None = None,
    ) -> ToolChunk:
        """Execute the edit and return the result."""
        # Validate file_path is absolute
        if not os.path.isabs(file_path):
            return ToolChunk(
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

        # Check file exists
        if not os.path.exists(file_path):
            return ToolChunk(
                content=[
                    TextBlock(text=f"Error: File not found: {file_path}"),
                ],
                state="error",
                is_last=True,
            )

        # Check old_string != new_string
        if old_string == new_string:
            return ToolChunk(
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

        content = None
        if _agent_state is not None:
            cache = await _agent_state.tool_context.get_cache(file_path)
            if cache is None:
                # Haven't read this file before
                return ToolChunk(
                    content=[
                        TextBlock(
                            text="Error: To edit a file, you must first read "
                            "it using the Read tool.",
                        ),
                    ],
                    state="error",
                    is_last=True,
                )
            content = "".join(cache.lines)
        else:
            # No state provided, read from disk
            try:
                async with aiofiles.open(
                    file_path,
                    "r",
                    encoding="utf-8",
                ) as f:
                    content = await f.read()
            except Exception as e:
                return ToolChunk(
                    content=[TextBlock(text=f"Error reading file: {str(e)}")],
                    state="error",
                    is_last=True,
                )

        # Count occurrences
        occurrences = content.count(old_string)

        # If occurrences == 0, raise error
        if occurrences == 0:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Error: old_string not found in {file_path}",
                    ),
                ],
                state="error",
                is_last=True,
            )

        # If occurrences > 1 and not replace_all, raise error
        if occurrences > 1 and not replace_all:
            return ToolChunk(
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
            return ToolChunk(
                content=[TextBlock(text=f"Error writing file: {str(e)}")],
                state="error",
                is_last=True,
            )

        # Return success message
        replacement_msg = (
            f"all {occurrences} occurrences" if replace_all else "1 occurrence"
        )
        return ToolChunk(
            content=[
                TextBlock(
                    text=f"Successfully replaced {replacement_msg} "
                    f"in {file_path}",
                ),
            ],
            state="running",
            is_last=True,
        )
