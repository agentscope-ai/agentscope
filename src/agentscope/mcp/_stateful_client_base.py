# -*- coding: utf-8 -*-
"""The base MCP stateful client class in AgentScope, that provides basic
 functionality for stateful MCP clients."""
from abc import ABC
from contextlib import AsyncExitStack
from typing import List

import mcp
from mcp import ClientSession

from ._client_base import MCPClientBase
from ._mcp_function import MCPToolFunction
from .._logging import logger


class StatefulClientBase(MCPClientBase, ABC):
    """The base class for stateful MCP clients in AgentScope, which maintains
    the session state across multiple tool calls.

    The developers should use `connect()` and `close()` methods to manage
    the client lifecycle.
    """

    is_connected: bool
    """If connected to the MCP server"""

    def __init__(self, name: str) -> None:
        """Initialize the stateful MCP client.

        Args:
            name (`str`):
                The name to identify the MCP server, which should be unique
                across the MCP servers.
        """

        super().__init__(name=name)

        self.client = None
        self.stack = None
        self.session = None
        self.is_connected = False

        # Cache the tools to avoid fetching them multiple times
        self._cached_tools = None

    async def connect(self) -> None:
        """Connect to MCP server."""
        if self.is_connected:
            raise RuntimeError(
                "The MCP server is already connected. Call close() "
                "before connecting again.",
            )

        self.stack = AsyncExitStack()

        try:
            context = await self.stack.enter_async_context(
                self.client,
            )
            read_stream, write_stream = context[0], context[1]
            self.session = ClientSession(read_stream, write_stream)
            await self.stack.enter_async_context(self.session)
            await self.session.initialize()

            self.is_connected = True
            logger.info("MCP client connected.")
        except Exception:
            await self.stack.aclose()
            self.stack = None
            raise

    async def close(self) -> None:
        """Clean up the MCP client resources. You must call this method when
        your application is done."""
        if not self.is_connected:
            raise RuntimeError(
                "The MCP server is not connected. Call connect() before "
                "closing.",
            )

        try:
            await self.stack.aclose()
            logger.info("MCP client closed.")
        except Exception as e:
            logger.warning("Error during MCP client cleanup: %s", e)
        finally:
            self.stack = None
            self.session = None
            self.is_connected = False

    async def list_tools(self) -> List[mcp.types.Tool]:
        """Get all available tools from the server.

        Returns:
            `mcp.types.ListToolsResult`:
                A list of available MCP tools.
        """
        self._validate_connection()

        res = await self.session.list_tools()

        # Cache the tools for later use
        self._cached_tools = res.tools
        return res.tools

    async def get_callable_function(
        self,
        func_name: str,
        wrap_tool_result: bool = True,
    ) -> MCPToolFunction:
        """Get an async tool function from the MCP server by its name, so
        that you can call it directly, wrap it into your own function, or
        anyway you like.

        .. note:: Currently, only the text, image, and audio results are
         supported in this function.

        Args:
            func_name (`str`):
                The name of the tool function to get.
            wrap_tool_result (`bool`):
                Whether to wrap the tool result into agentscope's
                `ToolResponse` object. If `False`, the raw result type
                `mcp.types.CallToolResult` will be returned.

        Returns:
            `MCPToolFunction`:
                A callable async function that returns either
                `mcp.types.CallToolResult` or `ToolResponse` when called.
        """
        self._validate_connection()

        if self._cached_tools is None:
            await self.list_tools()

        target_tool = None
        for tool in self._cached_tools:
            if tool.name == func_name:
                target_tool = tool
                break

        if target_tool is None:
            raise ValueError(
                f"Tool '{func_name}' not found in the MCP server",
            )

        return MCPToolFunction(
            mcp_name=self.name,
            tool=target_tool,
            wrap_tool_result=wrap_tool_result,
            session=self.session,
        )

    def _validate_connection(self) -> None:
        """Validate the connection to the MCP server."""
        if not self.is_connected:
            raise RuntimeError(
                "The connection is not established. Call connect() "
                "before using the client.",
            )

        if not self.session:
            raise RuntimeError(
                "The session is not initialized. Call connect() "
                "before using the client.",
            )
