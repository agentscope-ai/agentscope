# -*- coding: utf-8 -*-
"""The write tool in agentscope."""
import fnmatch
import os
from pathlib import Path
from typing import Any, List

import aiofiles

from .._base import ToolBase, ToolMiddlewareBase
from .._constants import (
    DEFAULT_DANGEROUS_FILES,
    DEFAULT_DANGEROUS_DIRECTORIES,
)
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
)
from .._response import ToolChunk
from ...message import TextBlock, ToolResultState
from ...state import AgentState


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
    is_external_tool: bool = False
    is_state_injected: bool = True

    def __init__(  # pylint: disable=dangerous-default-value
        self,
        dangerous_files: list[str] = DEFAULT_DANGEROUS_FILES,
        dangerous_directories: list[str] = DEFAULT_DANGEROUS_DIRECTORIES,
        middlewares: List[ToolMiddlewareBase] | None = None,
    ) -> None:
        """Initialize the write tool.

        Args:
            dangerous_files (`list[str]`, optional):
                Sensitive files that require explicit user confirmation,
                even in BYPASS mode. Matched by basename
                (case-insensitive). Defaults to `DEFAULT_DANGEROUS_FILES`.
                Pass a custom list to fully replace the defaults, or `[]`
                to disable the filename check.
            dangerous_directories (`list[str]`, optional):
                Sensitive directories that require explicit user
                confirmation. Matched when any path segment equals an
                entry (case-insensitive). Defaults to
                `DEFAULT_DANGEROUS_DIRECTORIES`. Pass a custom list to
                fully replace the defaults, or `[]` to disable the
                directory check.
            middlewares (`List[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
        """
        super().__init__(middlewares=middlewares)
        self.dangerous_files = list(dangerous_files)
        self.dangerous_directories = list(dangerous_directories)

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
                bypass_immune=True,
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

        # 3. Return PASSTHROUGH to let PermissionEngine check allow rules
        # This ensures allow rules can grant Write permissions
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="",
        )

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

    async def call(  # type: ignore[override]
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
                state=ToolResultState.ERROR,
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
                    state=ToolResultState.ERROR,
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
            state=ToolResultState.RUNNING,
            is_last=True,
        )
