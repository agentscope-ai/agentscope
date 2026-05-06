# -*- coding: utf-8 -*-
""""""

class MCPManager:
    """The MCP manager, responsible for managing MCP lifecycle within the agent
    service.

    For stateful MCP clients, the MCP connection will be created once created.
    """

    def __init__(self) -> None:
        """Initialize the MCP manager."""
        self._mcp_clients = []

    def get_mcp_client(self) -> None:
        """Get the MCP client."""

    def register_mcp_config(self) -> None:
