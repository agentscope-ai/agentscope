# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
"""The tool protocol in agentscope."""
import os
from abc import abstractmethod, ABC
from pathlib import Path
from typing import AsyncGenerator, Any, List

from pydantic import BaseModel

from ._constants import DEFAULT_DANGEROUS_FILES, DEFAULT_DANGEROUS_DIRECTORIES
from ..permission import (
    PermissionContext,
    PermissionDecision,
    PermissionRule,
    PermissionBehavior,
)
from ._response import ToolChunk
from ._utils import _remove_title_field


class _ParamsBase(BaseModel):
    """A base class for tool parameters that remove the title field from the
    exported JSON schema.
    """

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict:
        """An override implementation to remove the title field from the
        exported schema.
        """
        return _remove_title_field(super().model_json_schema(*args, **kwargs))


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
    is_external_tool: bool = False
    """If this tool is an external tool, which doesn't need to implement the
    __call__ method and the agent will yield the external tool call event."""
    is_state_injected: bool = False
    """If this tool requires agent state to be injected when called. If `True`,
    the state will be injected by an argument named `_agent_state`. Note your
    tool should be able to accept such argument.
    """
    is_mcp: bool = False
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

    async def check_read_only(
        self,
        tool_input: dict[str, Any],
    ) -> bool:
        """Decide whether this specific invocation is read-only.

        Returns the static :attr:`is_read_only` attribute by default.
        Subclasses with input-dependent semantics (e.g. ``Bash``) should
        override this to inspect ``tool_input`` — for example, ``Bash`` is
        statically marked as not read-only but ``ls -a`` is in fact read-only.

        Should be cheap — the permission engine may call this before the
        full :meth:`check_permissions` flow.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data for this invocation.

        Returns:
            `bool`:
                ``True`` if this invocation is read-only, ``False`` otherwise.
        """
        return self.is_read_only

    def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        """Check if a permission rule matches the tool input.

        .. note:: This is an optional method. A rule with no content (``None``)
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

        .. note:: Suggest a single tool-name-level rule (``rule_content=None``)
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

    def _path_in_allowed_working_path(
        self,
        file_path: str,
        context: PermissionContext,
    ) -> bool:
        """Check if a file path is within any allowed working directory.

        A "working directory" is the process's current directory plus any
        entries in :attr:`PermissionContext.working_directories`. Paths
        are compared via :func:`os.path.realpath` so that aliases like
        macOS's ``/tmp`` → ``/private/tmp`` and symlinked working
        directories compare equal on both sides.

        Used by tools that conditionally auto-allow file operations in
        :attr:`PermissionMode.ACCEPT_EDITS` (e.g. Write, Edit, and the
        filesystem-command branch of Bash).

        Args:
            file_path (`str`):
                The file path to check.
            context (`PermissionContext`):
                The permission context containing the working directories.

        Returns:
            `bool`:
                True if ``file_path`` is within any allowed working
                directory.
        """
        current_dir = os.getcwd()
        additional_dirs = list(context.working_directories.keys())
        all_working_dirs = [current_dir] + additional_dirs

        abs_file_path = os.path.realpath(os.path.expanduser(file_path))

        for working_dir in all_working_dirs:
            abs_working_dir = os.path.realpath(
                os.path.expanduser(working_dir),
            )
            try:
                os.path.relpath(abs_file_path, abs_working_dir)
                if (
                    abs_file_path.startswith(abs_working_dir + os.sep)
                    or abs_file_path == abs_working_dir
                ):
                    return True
            except ValueError:
                # On Windows, relpath raises ValueError if paths are on
                # different drives.
                continue

        return False

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
