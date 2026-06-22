# -*- coding: utf-8 -*-
"""Lazy MCP tool providers used by actor-scoped workspaces."""

from typing import Any, Protocol, TYPE_CHECKING

from ._mcp_client import MCPClient

if TYPE_CHECKING:
    from ..tool import ToolBase
else:
    ToolBase = Any


class MCPProvider(Protocol):
    """Provide MCP tools without exposing connection ownership to Toolkit."""

    name: str

    async def get_tools(self) -> list[ToolBase]:
        """Materialize tools for the provider's bound actor scope."""


class LegacyMCPClientProvider:
    """Adapt an existing connected MCPClient to :class:`MCPProvider`."""

    def __init__(self, client: MCPClient) -> None:
        self.name = client.name
        self.client = client

    async def get_tools(self) -> list[ToolBase]:
        """Delegate tool discovery to the wrapped legacy client."""
        return await self.client.list_tools()
