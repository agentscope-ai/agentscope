# -*- coding: utf-8 -*-
"""The MCP module in AgentScope, that provides fine-grained control over
the MCP servers."""

from ._config import StdioMCPConfig, HttpMCPConfig
from ._mcp_client import MCPClient
from ._provider import LegacyMCPClientProvider, MCPProvider
from ._connection_pool import (
    MCPConnectionKey,
    MCPConnectionPool,
    MCPDefinition,
    MCPResourcePolicy,
    ScopedMCPProvider,
    ScopedMCPTool,
)

__all__ = [
    "MCPClient",
    "MCPConnectionKey",
    "MCPConnectionPool",
    "MCPDefinition",
    "MCPResourcePolicy",
    "StdioMCPConfig",
    "HttpMCPConfig",
    "LegacyMCPClientProvider",
    "MCPProvider",
    "ScopedMCPProvider",
    "ScopedMCPTool",
]
