# -*- coding: utf-8 -*-
"""The tool protocol in agentscope."""
import fnmatch
import os
from abc import abstractmethod, ABC
from pathlib import Path
from typing import AsyncGenerator, Any, List

from ._constants import DEFAULT_DANGEROUS_FILES, DEFAULT_DANGEROUS_DIRECTORIES
from ._permission import (
    PermissionContext,
    PermissionDecision,
    PermissionRule,
    PermissionBehavior,
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

        Default implementation: matches rule_content as a glob pattern
        against the "file_path" key in tool_input. Tools that don't use
        file_path (e.g., Bash, Glob, Grep) should override this method.

        Args:
            rule_content (`str`):
                Glob pattern to match against the file path
            tool_input (`dict[str, Any]`):
                The tool input data

        Returns:
            `bool`:
                True if the rule matches, False otherwise
        """

        file_path = tool_input.get("file_path", "")
        if not file_path:
            return False
        return fnmatch.fnmatch(file_path, rule_content)

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List["PermissionRule"]:
        """Generate suggested permission rules for the tool input.

        Default implementation: suggests a glob pattern covering the parent
        directory of the "file_path" key. Tools that don't use file_path
        (e.g., Bash, Glob, Grep) should override this method.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data

        Returns:
            `List[PermissionRule]`:
                List of suggested permission rules
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
