# -*- coding: utf-8 -*-
"""MCP configuration for the agent service."""
import time
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


class MCPServiceConfig(BaseModel):
    """MCP configuration for the agent service.

    This extends the base MCP config with service-layer settings like
    connection scope and name.

    Example:
        ```python
        config = MCPServiceConfig(
            name="weather_api",
            connection_scope=ConnectionScope.SHARED,
            mcp_config=HttpMCPConfig(
                type="http_mcp",
                url="https://api.weather.com/mcp"
            )
        )
        ```
    """

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

    created_at: float = Field(
        default_factory=time.time,
        init=False,
        description="Creation timestamp (Unix epoch).",
    )

    updated_at: float = Field(
        default_factory=time.time,
        init=False,
        description="Last-updated timestamp (Unix epoch).",
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


class MCPListResponse(BaseModel):
    """Response model for listing MCP configurations."""

    mcps: list[MCPServiceConfig] = Field(description="List of MCP records.")
    total: int = Field(description="Total number of MCP configurations.")


class CreateMCPResponse(BaseModel):
    """Response model after creating an MCP configuration."""

    name: str = Field(
        description="Unique name of the newly created MCP configuration.",
    )


class UpdateMCPRequest(BaseModel):
    """Request body for partially updating an MCP configuration.

    All fields are optional; omit any field to keep its current value.
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
