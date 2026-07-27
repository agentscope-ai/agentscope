# -*- coding: utf-8 -*-
""""""
from abc import ABC, abstractmethod

from ....mcp import MCPClient


class MCPHubBase(ABC):
    """The base class for MCP Hub implementations."""

    @abstractmethod
    async def list_mcps(
        self,
        user_id: str,
    ) -> list[MCPClient]:
        """Get all the available MCP clients.

        Args:
            user_id (`str`):
                The user identifier to query MCP clients for.
        """
