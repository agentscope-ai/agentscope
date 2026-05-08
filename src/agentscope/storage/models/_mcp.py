# -*- coding: utf-8 -*-
"""Storage models for MCP configurations."""
from pydantic import BaseModel, Field

from ...mcp import StdioMCPConfig, HttpMCPConfig
from ...app._schema import ConnectionScope


class MCPModel(BaseModel):
    """Storage model for MCP configuration.

    This represents the complete data structure stored in the database,
    including both user-provided fields and server-generated metadata.
    """

    name: str = Field(
        description="The unique name to identify this MCP configuration.",
    )

    connection_scope: ConnectionScope = Field(
        description="The connection scope and lifecycle strategy.",
    )

    mcp_config: StdioMCPConfig | HttpMCPConfig = Field(
        discriminator="type",
        description="The base MCP server configuration.",
    )

    creator_id: str = Field(
        description="User ID of the creator.",
    )

    created_at: float = Field(
        description="Creation timestamp (Unix epoch).",
    )

    updated_at: float = Field(
        description="Last-updated timestamp (Unix epoch).",
    )
