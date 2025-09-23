# -*- coding: utf-8 -*-
"""An SSE MCP server with a simple multiply tool function."""

from mcp.server import FastMCP


mcp = FastMCP("Multiply", port=8002)


@mcp.tool()
def multiply(c: int, d: int) -> int:
    """Multiply two numbers."""
    return c * d


mcp.run(transport="streamable-http")
