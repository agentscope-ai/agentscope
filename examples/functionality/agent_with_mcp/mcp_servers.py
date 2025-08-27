# -*- coding: utf-8 -*-

"""This module contains the server setup for FastMCP applications."""

from contextlib import asynccontextmanager, AsyncExitStack
from typing import AsyncGenerator, Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import Lifespan

# Define the addition tool
mcp_add = FastMCP("Add")


@mcp_add.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


# Define the multiplication tool
mcp_multiply = FastMCP("Multiply")


@mcp_multiply.tool()
def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b


def combine_lifespans(*lifespans: Any) -> Lifespan[Any]:
    """Combine multiple lifespans into one asynchronous context manager."""

    @asynccontextmanager
    async def combined_lifespan(app: Any) -> AsyncGenerator[None, Any]:
        async with AsyncExitStack() as stack:
            for lifespan in lifespans:
                ctx = lifespan(app)
                await stack.enter_async_context(ctx)
            yield

    return combined_lifespan


# Create the main application
main_app = Starlette(
    routes=[
        Mount("/sse_app/", app=mcp_add.sse_app()),
        Mount("/streamable_http_app/", app=mcp_multiply.streamable_http_app()),
    ],
    lifespan=combine_lifespans(lambda app: mcp_multiply.session_manager.run()),
)

if __name__ == "__main__":
    uvicorn.run(main_app, host="0.0.0.0", port=8001)
