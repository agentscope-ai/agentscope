# -*- coding: utf-8 -*-
"""The tool protocol in agentscope."""
from typing import Protocol, runtime_checkable, AsyncGenerator, Any

from ._response import ToolChunk


@runtime_checkable
class ToolProtocol(Protocol):

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

    async def check_permissions(self, tool_input: dict[str, Any], context: "PermissionContext") -> "PermissionDecision":
        """Check permissions for the tool usage."""
        ...

    async def __call__(self, **kwargs) -> ToolChunk | AsyncGenerator[ToolChunk, None]:
        """Invoke the tool with the given arguments."""
        ...

