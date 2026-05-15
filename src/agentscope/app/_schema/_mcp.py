# -*- coding: utf-8 -*-
"""MCP schemas for API requests and responses."""
from enum import Enum

from pydantic import BaseModel, Field

from ...mcp import StdioMCPConfig, HttpMCPConfig


class ConnectionScope(str, Enum):
    """MCP connection scope and lifecycle strategy.

    This determines how MCP connections are managed in the service layer.
    """

    SHARED = "shared"
    """Shared connection across all agents/users.
    - One connection per MCP config, shared globally
    - Created on first use, destroyed on application shutdown
    - Use case: Stateless HTTP MCP (e.g., weather API, web search)
    """

    ISOLATED = "isolated"
    """Isolated connection per agent.
    - One connection per (MCP config, agent)
    - Created on first use per agent, destroyed on agent session end
    - Use case: Stateful MCP (e.g., browser-use), STDIO MCP
    """

    EPHEMERAL = "ephemeral"
    """Ephemeral connection per request.
    - New connection created for each request, destroyed immediately after
    - No connection pooling
    - Use case: Low-frequency stateless HTTP MCP
    """


class MCPBase(BaseModel):
    """Base MCP fields shared across request/response schemas."""

    name: str = Field(
        title="MCP Name",
        description="The unique name to identify this MCP configuration.",
    )

    connection_scope: ConnectionScope = Field(
        title="Connection Scope",
        description="The connection scope and lifecycle strategy.",
    )

    mcp_config: StdioMCPConfig | HttpMCPConfig = Field(
        discriminator="type",
        title="MCP Config",
        description="The base MCP server configuration.",
    )

    def validate_config(self) -> None:
        """Validate the configuration.

        Raises:
            ValueError: If the configuration is invalid.
        """
        # STDIO MCP cannot use ephemeral mode
        if (
            self.mcp_config.type == "stdio_mcp"
            and self.connection_scope == ConnectionScope.EPHEMERAL
        ):
            raise ValueError(
                "STDIO MCP does not support ephemeral mode. "
                "Use 'shared' or 'isolated' instead.",
            )


class MCPCreateRequest(MCPBase):
    """Request body for creating a new MCP configuration.

    Used in POST /mcp endpoint. Does not include server-generated fields
    like creator_id, created_at, updated_at.
    """

    pass


class MCPUpdateRequest(BaseModel):
    """Request body for partially updating an MCP configuration.

    Used in PATCH /mcp/{name} endpoint. All fields are optional.
    """

    connection_scope: ConnectionScope | None = Field(
        default=None,
        description="New connection scope.",
    )
    mcp_config: StdioMCPConfig | HttpMCPConfig | None = Field(
        default=None,
        discriminator="type",
        description="New MCP server configuration.",
    )


class MCPResponse(MCPBase):
    """Response model for MCP configuration with server-generated metadata.

    Used in GET /mcp/{name}, GET /mcp (list), and POST /mcp responses.
    Includes all fields from MCPBase plus server-assigned metadata.
    """

    creator_id: str = Field(
        description="User ID of the creator.",
    )

    created_at: float = Field(
        description="Creation timestamp (Unix epoch).",
    )

    updated_at: float = Field(
        description="Last-updated timestamp (Unix epoch).",
    )


class MCPListResponse(BaseModel):
    """Response model for listing MCP configurations."""

    mcps: list[MCPResponse] = Field(
        description="List of MCP configurations.",
    )
    total: int = Field(
        description="Total number of MCP configurations.",
    )
