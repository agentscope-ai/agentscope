# -*- coding: utf-8 -*-
"""An SSE MCP server with a simple add one tool function."""

from mcp.server import FastMCP


mcp = FastMCP("Add_one", port=8001)


@mcp.tool()
def add_one(a: int) -> int:
    """Add one to the input number"""
    return a + 1


mcp.run(transport="sse")
