# -*- coding: utf-8 -*-
"""The base class for MCP clients in AgentScope."""
from abc import abstractmethod
from typing import Any, TYPE_CHECKING, List

import mcp

if TYPE_CHECKING:
    from ..tool import MCPTool
else:
    MCPTool = Any


class MCPClientBase:
    """Base class for MCP clients."""

    def __init__(self, name: str) -> None:
        """Initialize the MCP client with a name.

        Args:
            name (`str`):
                The name to identify the MCP server, which should be unique
                across the MCP servers.
        """
        self.name = name

    @abstractmethod
    async def get_tool(
        self,
        name: str,
    ) -> MCPTool:
        """Get a tool object by its name.

        Args:
            name (`str`):
                The name of the tool to get.

        Returns:
            `MCPTool`:
                A tool object that implements ToolProtocol.
        """

    @abstractmethod
    async def list_tools(self) -> List[mcp.types.Tool]:
        """List the MCP tools."""
