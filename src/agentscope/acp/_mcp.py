# -*- coding: utf-8 -*-
"""Convert client-supplied MCP servers into session-local clients."""

from typing import TypeAlias
from urllib.parse import urlsplit

from acp.schema import HttpMcpServer, McpServerStdio, SseMcpServer

from ..mcp import MCPClient, StdioMCPConfig, HttpMCPConfig

MCPServer: TypeAlias = HttpMcpServer | McpServerStdio | SseMcpServer


def build_mcp_clients(
    servers: list[MCPServer],
    cwd: str,
) -> list[MCPClient]:
    """Validate names and preserve each server's transport configuration."""
    clients = []
    names: set[str] = set()
    for server in servers:
        if not server.name or server.name in names:
            raise ValueError(f"Invalid or duplicate MCP name: {server.name}")
        names.add(server.name)
        config: StdioMCPConfig | HttpMCPConfig
        if isinstance(server, McpServerStdio):
            config = StdioMCPConfig(
                command=server.command,
                args=server.args,
                env={entry.name: entry.value for entry in server.env},
                cwd=cwd,
            )
        else:
            # MCPClient infers legacy SSE from these URL paths as well.
            path = urlsplit(server.url).path
            if isinstance(server, SseMcpServer) or path.endswith(
                ("/sse", "/messages/"),
            ):
                raise ValueError(
                    f"Legacy SSE MCP is not supported: {server.name}",
                )
            config = HttpMCPConfig(
                url=server.url,
                headers={entry.name: entry.value for entry in server.headers},
            )
        clients.append(
            MCPClient(
                name=server.name,
                is_stateful=isinstance(config, StdioMCPConfig),
                mcp_config=config,
            ),
        )
    return clients
