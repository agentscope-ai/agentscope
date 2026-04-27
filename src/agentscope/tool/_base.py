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
    is_external_tool: bool
    """If this tool is an external tool, which doesn't need to implement the
    __call__ method and the agent will yield the external tool call event."""
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
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        """Check if a permission rule matches the tool input.

        .. note:: This is an optional method. The default implementation
        mirrors Claude Code's behavior: a rule with no content (``None``)
        is a tool-name-level rule that matches every invocation; a rule
        with content requires the tool to override this method with its
        own matching logic, otherwise it returns ``False``.

        This means:
        - ``_FunctionTool`` and ``MCPTool`` (which do not override this)
          can still be controlled at the tool-name level via rules like
          ``{"tool_name": "my_tool", "rule_content": None}``.
        - Specific tools (Bash, Read, Write, Edit, Glob, Grep) override
          this method to support fine-grained pattern matching.

        Args:
            rule_content (`str | None`):
                The rule pattern to match. ``None`` means "match all
                invocations of this tool" (tool-name-level rule).
            tool_input (`dict[str, Any]`):
                The tool input data

        Returns:
            `bool`:
                True if the rule matches, False otherwise
        """
        # None rule_content = tool-name-level rule, matches everything
        return rule_content is None

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        """Generate suggested permission rules for the tool input.

        .. note:: The default implementation mirrors Claude Code's MCP tool
        behavior: suggest a single tool-name-level rule (``rule_content=None``)
        that allows all invocations of this tool. Tools can override this to
        provide finer-grained suggestions.

        For example:
        - File tools (Read/Write/Edit): suggest a glob pattern covering the
          parent directory (e.g., "src/main.py" -> "src/**")
        - Bash: suggest command prefix patterns (e.g., "git commit -m 'xxx'"
          -> "git commit:*")
        - Grep/Glob: suggest patterns based on search paths

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data

        Returns:
            `List[PermissionRule]`:
                List of suggested permission rules (usually 1, max 5 for
                compound operations)
        """
        return [
            PermissionRule(
                tool_name=self.name,
                rule_content=None,
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

    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ToolChunk | AsyncGenerator[ToolChunk, None]:
        """Invoke the tool with the given arguments."""
        if not self.is_external_tool:
            raise NotImplementedError(
                f"{self.__class__.__name__} does not implement __call__",
            )

        raise RuntimeError(
            f"{self.__class__.__name__} is an external tool and should not "
            f"be called directly",
        )
