# -*- coding: utf-8 -*-
"""The MCP streamable HTTP server."""
from contextlib import _AsyncGeneratorContextManager
from typing import Any, Callable, Awaitable, Literal, List

import mcp.types
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from ._client_base import MCPClientBase
from ..tool import MCPTool


class HttpStatelessClient(MCPClientBase):
    """The sse/streamable HTTP MCP client implementation in AgentScope.

    .. note:: Note this client is stateless, meaning it won't maintain the
     session state across multiple tool calls. Each tool call will start a
     new session and close it after the call is done.

    """

    stateful: bool = False
    """Whether the MCP server is stateful, meaning it will maintain the
    session state across multiple tool calls, or stateless, meaning it
    will start a new session for each tool call."""

    def __init__(
        self,
        name: str,
        transport: Literal["streamable_http", "sse"],
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
        sse_read_timeout: float = 60 * 5,
        **client_kwargs: Any,
    ) -> None:
        """Initialize the streamable HTTP MCP server.

        Args:
            name (`str`):
                The name to identify the MCP server, which should be unique
                across the MCP servers.
            transport (`Literal["streamable_http", "sse"]`):
                The transport type of MCP server. Generally, the URL of sse
                transport should end with `/sse`, while the streamable HTTP
                URL ends with `/mcp`.
            url (`str`):
                The URL of the MCP server.
            headers (`dict[str, str] | None`, optional):
                Additional headers to include in the HTTP request.
            timeout (`float`, optional):
                The timeout for the HTTP request in seconds. Defaults to 30.
            sse_read_timeout (`float`, optional):
                The timeout for reading Server-Sent Events (SSE) in seconds.
                Defaults to 300 (5 minutes).
            **client_kwargs (`Any`):
                The additional keyword arguments to pass to the streamable
                HTTP client.
        """
        super().__init__(name=name)

        assert transport in ["streamable_http", "sse"]

        self.transport = transport

        self.client_config = {
            "url": url,
            "headers": headers or {},
            "timeout": timeout,
            "sse_read_timeout": sse_read_timeout,
            **client_kwargs,
        }

        self._tools = None

    def get_client(self) -> _AsyncGeneratorContextManager[Any]:
        """The disposable MCP client object, which is a context manager."""
        if self.transport == "sse":
            return sse_client(**self.client_config)

        if self.transport == "streamable_http":
            return streamable_http_client(**self.client_config)

        raise ValueError(
            f"Unsupported transport type: {self.transport}. "
            "Supported types are 'sse' and 'streamable_http'.",
        )

    async def get_tool(
        self,
        func_name: str,
        execution_timeout: float | None = None,
    ) -> MCPTool:
        """Get a tool object by its name.

        The returned MCPTool object implements ToolProtocol and can be:
        - Called directly: `await tool(arg1=val1)`
        - Registered to toolkit: `toolkit.register_tool(tool)`

        Args:
            func_name (`str`):
                The name of the tool function.
            execution_timeout (`float | None`, optional):
                The preset timeout in seconds for calling the tool function.

        Returns:
            `MCPTool`:
                A tool object that implements ToolProtocol.
        """

        if self._tools is None:
            await self.list_tools()

        target_tool = None
        for tool in self._tools:
            if tool.name == func_name:
                target_tool = tool
                break

        if target_tool is None:
            raise ValueError(
                f"Tool '{func_name}' not found in the MCP server ",
            )

        return MCPTool(
            mcp_name=self.name,
            tool=target_tool,
            client_gen=self.get_client,
            timeout=execution_timeout,
        )

    async def list_tools(self) -> List[mcp.types.Tool]:
        """List all tools available on the MCP server.

        Returns:
            `mcp.types.ListToolsResult`:
                The result containing the list of tools.
        """
        async with self.get_client() as cli:
            read_stream, write_stream = cli[0], cli[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                res = await session.list_tools()
                self._tools = res.tools
                return res.tools
