# -*- coding: utf-8 -*-
"""The base class for MCP clients in AgentScope."""
from abc import abstractmethod
from typing import List

import mcp.types

from .._logging import logger


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
        func_name: str,
    ) -> "MCPTool":
        """Get a tool object by its name.

        Args:
            func_name (`str`):
                The name of the tool to get.

        Returns:
            `MCPTool`:
                A tool object that implements ToolProtocol.
        """
