# -*- coding: utf-8 -*-
"""An SSE MCP server with a simple add tool function."""

from mcp.server import FastMCP


mcp = FastMCP("Add", port=8001)


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


mcp.run(transport="sse")
