# -*- coding: utf-8 -*-
"""The tool protocol in agentscope."""
from abc import abstractmethod
from typing import AsyncGenerator, Any

from ._permission import PermissionContext, PermissionDecision
from ._response import ToolChunk


class ToolBase:
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

    @abstractmethod
    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool usage."""

    @abstractmethod
    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ToolChunk | AsyncGenerator[ToolChunk, None]:
        """Invoke the tool with the given arguments."""
