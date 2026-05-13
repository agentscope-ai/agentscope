# -*- coding: utf-8 -*-
"""Unified MCP client implementation for AgentScope."""
from contextlib import AsyncExitStack, _AsyncGeneratorContextManager
from typing import Any, TYPE_CHECKING

import httpx
import mcp.types
from mcp import ClientSession, stdio_client, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import Field, BaseModel, PrivateAttr

from ._config import StdioMCPConfig, HttpMCPConfig
from .._logging import logger

if TYPE_CHECKING:
    from ..tool import MCPTool
else:
    MCPTool = Any


class MCPClient(BaseModel):
    """The unified MCP client in AgentScope.

    This class provides a unified interface for MCP connections, handling both
    stateful (persistent) and stateless (ephemeral) connections.

    - Stateful: Requires explicit connect() and close(), maintains session
    - Stateless: No connect() needed, creates temporary session per call

    Private attributes:
    - _client: The underlying MCP client context manager
    - _session: The MCP ClientSession (for stateful connections only)
    - _stack: AsyncExitStack for managing connection lifecycle
    - _is_connected: Connection state flag
    - _cached_tools: Cached list of tools

    Example:

    .. code-block:: python

        # Stateful connection (STDIO or HTTP)
        client = MCPClient(
            name="file_system",
            is_stateful=True,
            mcp_config=StdioMCPConfig(
                command="mcp-server-filesystem"
            )
        )
        await client.connect()
        tools = await client.list_tools()
        await client.close()

        # Stateless connection (HTTP only)
        client = MCPClient(
            name="weather_search",
            is_stateful=False,
            mcp_config=HttpMCPConfig(
                url="https://api.weather.com/mcp"
            )
        )
        # No connect() needed
        tools = await client.list_tools()

    """

    name: str = Field(
        title="MCP Name",
        description="The MCP name.",
    )

    is_stateful: bool = Field(
        title="Stateful",
        description=(
            "Whether this is a stateful connection that requires explicit "
            "connect() and close(). STDIO MCP must be stateful. HTTP MCP "
            "can be either stateful or stateless."
        ),
    )

    mcp_config: StdioMCPConfig | HttpMCPConfig = Field(
        discriminator="type",
        title="MCP Config",
        description="The MCP server configuration.",
    )

    # Private attributes
    _client: Any = PrivateAttr(default=None)
    _session: ClientSession | None = PrivateAttr(default=None)
    _stack: AsyncExitStack | None = PrivateAttr(default=None)
    _is_connected: bool = PrivateAttr(default=False)
    _cached_tools: list[mcp.types.Tool] | None = PrivateAttr(default=None)

    @property
    def is_connected(self) -> bool:
        """Whether the client is currently connected.

        Returns:
            True if connected, False otherwise.
        """
        return self._is_connected

    def model_post_init(self, __context: Any) -> None:
        """Validate configuration and initialize client."""
        # STDIO MCP must be stateful
        if self.mcp_config.type == "stdio_mcp" and not self.is_stateful:
            raise ValueError(
                "STDIO MCP must be stateful (is_stateful=True).",
            )

        # Initialize the underlying client
        self._initialize_client()

    def _initialize_client(self) -> None:
        """Initialize the underlying MCP client based on config type."""
        if self.mcp_config.type == "stdio_mcp":
            config = self.mcp_config
            self._client = stdio_client(
                StdioServerParameters(
                    command=config.command,
                    args=config.args or [],
                    env=config.env,
                    cwd=str(config.cwd) if config.cwd else None,
                    encoding="utf-8",
                    encoding_error_handler=config.encoding_error_handler,
                ),
            )

    def _create_http_client(
        self,
    ) -> _AsyncGeneratorContextManager[Any]:
        """Create an HTTP MCP client (SSE or streamable HTTP)."""
        config = self.mcp_config

        # Determine transport from URL
        if config.url.endswith("/sse") or config.url.endswith("/messages/"):
            return sse_client(
                url=config.url,
                headers=config.headers,
                timeout=config.timeout,
            )

        # StreamableHTTP transport
        http_client = None
        if config.headers or config.timeout:
            http_client = httpx.AsyncClient(
                headers=config.headers,
                timeout=config.timeout,
            )
        return streamable_http_client(
            url=config.url,
            http_client=http_client,
        )

    async def connect(self) -> None:
        """Connect to the MCP server (for stateful connections only).

        For stateless connections, this method does nothing.

        Raises:
            RuntimeError: If already connected.
        """
        if not self.is_stateful:
            logger.debug(
                "Stateless MCP '%s' does not require explicit connect.",
                self.name,
            )
            return

        if self._is_connected:
            raise RuntimeError(
                f"MCP '{self.name}' is already connected. "
                "Call close() before reconnecting.",
            )

        # Create HTTP client if needed
        if self._client is None and self.mcp_config.type == "http_mcp":
            self._client = self._create_http_client()

        self._stack = AsyncExitStack()

        try:
            context = await self._stack.enter_async_context(self._client)
            read_stream, write_stream = context[0], context[1]
            self._session = ClientSession(read_stream, write_stream)
            await self._stack.enter_async_context(self._session)
            await self._session.initialize()

            self._is_connected = True
            logger.info("MCP connected: %s", self.name)
        except Exception:
            await self._stack.aclose()
            self._stack = None
            raise

    async def close(self, ignore_errors: bool = True) -> None:
        """Close the MCP connection (for stateful connections only).

        For stateless connections, this method does nothing.

        Args:
            ignore_errors: Whether to ignore errors during cleanup.

        Raises:
            RuntimeError: If not connected.
        """
        if not self.is_stateful:
            logger.debug(
                "Stateless MCP '%s' does not require explicit close.",
                self.name,
            )
            return

        if not self._is_connected:
            raise RuntimeError(
                f"MCP '{self.name}' is not connected. "
                "Call connect() first.",
            )

        try:
            await self._stack.aclose()
        except Exception as e:
            if not ignore_errors:
                raise e
            logger.warning(
                "Error closing MCP '%s': %s",
                self.name,
                str(e),
            )
        finally:
            self._stack = None
            self._session = None
            self._is_connected = False
            logger.info("MCP closed: %s", self.name)

    def _get_client_gen(self) -> _AsyncGeneratorContextManager[Any]:
        """Get client generator for stateless connections."""
        if self.mcp_config.type == "stdio_mcp":
            return self._client
        else:
            return self._create_http_client()

    async def list_tools(self) -> list[mcp.types.Tool]:
        """List all available tools from the MCP server.

        Returns:
            List of available MCP tools.

        Raises:
            RuntimeError: If not connected (for stateful connections).
        """
        if not self.is_stateful:
            # Stateless: create temporary session
            async with self._get_client_gen() as cli:
                read_stream, write_stream = cli[0], cli[1]
                async with ClientSession(
                    read_stream,
                    write_stream,
                ) as session:
                    await session.initialize()
                    res = await session.list_tools()
                    self._cached_tools = res.tools
                    return res.tools
        else:
            # Stateful: use existing session
            self._validate_connection()
            res = await self._session.list_tools()
            self._cached_tools = res.tools
            return res.tools

    async def get_tool(
        self,
        name: str,
        execution_timeout: float | None = None,
    ) -> MCPTool:
        """Get a tool by name from the MCP server.

        The returned MCPTool object implements ToolProtocol and can be:
        - Called directly: `await tool(arg1=val1)`
        - Registered to toolkit: `toolkit.register_tool(tool)`

        Args:
            name: The name of the tool function to get.
            execution_timeout: The preset timeout in seconds for calling
                the tool function.

        Returns:
            A tool object that implements ToolProtocol.

        Raises:
            ValueError: If the tool is not found.
            RuntimeError: If not connected (for stateful connections).
        """
        # Avoid circular import by importing here
        from ..tool import MCPTool

        # Fetch tools if not cached
        if self._cached_tools is None:
            await self.list_tools()

        # Find target tool
        target_tool = None
        for tool in self._cached_tools:
            if tool.name == name:
                target_tool = tool
                break

        if target_tool is None:
            raise ValueError(
                f"Tool '{name}' not found in MCP server " f"'{self.name}'",
            )

        # Create MCPTool based on stateful/stateless
        if not self.is_stateful:
            # Stateless: pass client generator
            return MCPTool(
                mcp_name=self.name,
                tool=target_tool,
                client_gen=self._get_client_gen,
                timeout=execution_timeout,
            )
        else:
            # Stateful: pass session
            self._validate_connection()
            return MCPTool(
                mcp_name=self.name,
                tool=target_tool,
                session=self._session,
                timeout=execution_timeout,
            )

    def _validate_connection(self) -> None:
        """Validate connection state for stateful connections.

        Raises:
            RuntimeError: If not connected or session not initialized.
        """
        if not self._is_connected:
            raise RuntimeError(
                f"MCP '{self.name}' is not connected. "
                "Call connect() first.",
            )
        if not self._session:
            raise RuntimeError(
                f"MCP '{self.name}' session is not initialized. "
                "Call connect() first.",
            )
