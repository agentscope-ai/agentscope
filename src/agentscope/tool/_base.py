# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
"""The tool protocol in agentscope."""
import os
from abc import abstractmethod, ABC
from pathlib import Path
from typing import AsyncGenerator, Any, List

from ._constants import DEFAULT_DANGEROUS_FILES, DEFAULT_DANGEROUS_DIRECTORIES
from ._permission import (
    PermissionContext,
    PermissionDecision,
    PermissionRule,
)
from ._response import ToolChunk


class ToolBase(ABC):
    """The tool protocol."""

    name: str
    """The name presented to the agent."""
    description: str
    """The agent-oriented tool description."""
    input_schema: dict[str, Any]
    """The input schema of the tool, following JSON schema format."""
    is_concurrency_safe: bool
    """If this tool is concurrency safe."""
    is_read_only: bool
    """If this tool is read-only, which will be used in the permission
    checking."""
    is_mcp: bool
    """If this tool is an MCP tool, which will be used in the permission"""
    mcp_name: str | None = None
    """The name of the MCP server this tool belongs to, which is required if
    this tool is an MCP tool."""

    # Class attributes for dangerous path checking
    dangerous_files: list[str] = DEFAULT_DANGEROUS_FILES
    """List of dangerous files that should be protected from auto-editing."""
    dangerous_directories: list[str] = DEFAULT_DANGEROUS_DIRECTORIES
    """List of dangerous directories that should be protected from
    auto-editing."""

    @abstractmethod
    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool usage."""

    def match_rule(
        self,
        rule_content: str,
        tool_input: dict[str, Any],
    ) -> bool:
        """Check if a permission rule matches the tool input.

        .. note:: This is an optional method. By default, it returns False
        (no match), so the permission engine treats any rule as a miss and
        falls through to asking the user. Tools should override this if they
        want rule-based auto-allow/deny to work.

        Each tool should implement its own matching logic based on its
        specific parameters. For example:
        - File tools (Read/Write/Edit): match rule_content as a glob pattern
          against the "file_path" parameter
        - Bash: match rule_content against the "command" parameter using
          regex/substring matching with support for prefix patterns (e.g.,
          "git:*") and wildcards
        - Grep/Glob: match rule_content against search patterns or paths

        Args:
            rule_content (`str`):
                The rule pattern to match (format depends on tool type)
            tool_input (`dict[str, Any]`):
                The tool input data

        Returns:
            `bool`:
                True if the rule matches, False otherwise
        """
        return False

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        """Generate suggested permission rules for the tool input.

        .. note:: this is an optional abstract method. By default, it returns
        an empty list, and tools can override it if they want to provide
        suggestions.

        Each tool must implement its own suggestion logic based on its
        specific parameters. The goal is to generate broader permission rules
        that can avoid future confirmation prompts. For example:
        - File tools (Read/Write/Edit): suggest a glob pattern covering the
          parent directory (e.g., "src/main.py" -> "src/**")
        - Bash: suggest command prefix patterns (e.g., "git commit -m 'xxx'"
          -> "git commit:*")
        - Grep/Glob: suggest patterns based on search paths or patterns

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data

        Returns:
            `List[PermissionRule]`:
                List of suggested permission rules (usually 1, max 5 for
                compound operations)
        """
        return []

    def _is_dangerous_path(self, file_path: str) -> bool:
        """Check if a file path is dangerous (sensitive file or directory).

        A path is considered dangerous if:
        1. The filename matches a dangerous file (e.g., .bashrc, .gitconfig)
        2. Any path segment matches a dangerous directory (e.g., .git, .ssh)

        Case-insensitive matching is used to prevent bypasses on
        case-insensitive filesystems (macOS, Windows).

        Args:
            file_path (`str`):
                The file path to check

        Returns:
            `bool`:
                True if the path is dangerous and should require explicit
                permission

        Example:
            >>> self._is_dangerous_path("/home/user/.bashrc")
            True
            >>> self._is_dangerous_path("/home/user/.git/config")
            True
            >>> self._is_dangerous_path("/home/user/project/main.py")
            False
        """

        # Normalize path
        abs_path = os.path.abspath(os.path.expanduser(file_path))

        # Split path into segments
        path_parts = Path(abs_path).parts
        path_parts_lower = [p.lower() for p in path_parts]

        # Check if filename matches dangerous files (case-insensitive)
        filename = os.path.basename(abs_path)
        filename_lower = filename.lower()
        for dangerous_file in self.dangerous_files:
            if filename_lower == dangerous_file.lower():
                return True

        # Check if any path segment matches dangerous directories
        # (case-insensitive)
        for dangerous_dir in self.dangerous_directories:
            dangerous_dir_lower = dangerous_dir.lower()
            if dangerous_dir_lower in path_parts_lower:
                return True

        return False

    @abstractmethod
    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ToolChunk | AsyncGenerator[ToolChunk, None]:
        """Invoke the tool with the given arguments."""
