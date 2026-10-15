# -*- coding: utf-8 -*-
"""Unified MCP client implementation for AgentScope."""
import asyncio
import re
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
)
from typing import Any, AsyncGenerator, ClassVar, TYPE_CHECKING
from urllib.parse import urlsplit

import httpx2
import mcp.types
from mcp import Client as SDKClient, stdio_client, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.streamable_http import (
    create_mcp_http_client,
    streamable_http_client,
)
from pydantic import Field, BaseModel, PrivateAttr

from ._config import StdioMCPConfig, HttpMCPConfig
from .._logging import logger

if TYPE_CHECKING:
    from ..tool import MCPTool, ToolBase
else:
    MCPTool = Any
    ToolBase = Any


class MCPClient(BaseModel):
    """The unified MCP client in AgentScope.

    This class provides a unified interface for MCP connections, handling both
    stateful (persistent) and stateless (ephemeral) connections.

    - Stateful: Requires explicit connect() and close(), maintains a client
    - Stateless: No connect() needed, creates a temporary client per call

    Private attributes:
    - _client: The connected MCP SDK client for stateful connections
    - _lifecycle_task: Owns the stateful client context from enter to exit
    - _close_requested: Signals the lifecycle task to exit the client context
    - _is_connected: Connection state flag
    - _cached_tools: Cached list of tools
    - _http_client: The live HTTP client, while one is open
    - _static_headers: Its headers before any runtime override
    - _runtime_headers: See :meth:`set_runtime_headers`

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

    # httpx2 derives these from the request URL and body with setdefault,
    # so a client-level value silently wins and breaks routing or framing.
    # The headers MCP itself sends are set per request and need no guard.
    _RESERVED_HEADERS: ClassVar[frozenset[str]] = frozenset(
        {"connection", "content-length", "host", "transfer-encoding"},
    )
    # RFC 7230 token, and a field value of visible ASCII plus tab.
    _HEADER_NAME: ClassVar[re.Pattern[str]] = re.compile(
        r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+",
    )
    _HEADER_VALUE: ClassVar[re.Pattern[str]] = re.compile(r"[\t\x20-\x7e]*")

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

    enable_tools: list[str] | None = None
    """The tools enabled in this MCP, which will be returned in the
    `list_tools` function. If `None`, all tools from the MCP server will be
    returned."""

    disable_tools: list[str] | None = None
    """The tools disabled in this MCP, which will be filtered out in the
    `list_tools` function."""

    execution_timeout: float | None = None
    """The execution timeout in seconds for calling the tools from this MCP."""

    # Private attributes
    _client: SDKClient | None = PrivateAttr(default=None)
    _lifecycle_task: asyncio.Task[None] | None = PrivateAttr(default=None)
    _close_requested: asyncio.Event | None = PrivateAttr(default=None)
    _is_connected: bool = PrivateAttr(default=False)
    _cached_tools: list[mcp.types.Tool] | None = PrivateAttr(default=None)
    _http_client: httpx2.AsyncClient | None = PrivateAttr(default=None)
    _static_headers: httpx2.Headers | None = PrivateAttr(default=None)
    _runtime_headers: dict[str, str] = PrivateAttr(default_factory=dict)

    @property
    def is_connected(self) -> bool:
        """Whether the client is currently connected.

        Returns:
            True if connected, False otherwise.
        """
        return self._is_connected

    def model_post_init(self, __context: Any) -> None:
        """Validate configuration and initialize client."""
        # MCP name is used to compose model-facing tool names
        # (mcp__{name}__{tool}), which must match ^[a-zA-Z0-9_-]+$.
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", self.name):
            raise ValueError(
                f"MCPClient name '{self.name}' contains characters not "
                f"allowed by LLM providers (only [a-zA-Z0-9_-] are "
                f"permitted). Please rename it.",
            )

        # STDIO MCP must be stateful
        if self.mcp_config.type == "stdio_mcp" and not self.is_stateful:
            raise ValueError(
                "STDIO MCP must be stateful (is_stateful=True).",
            )

        # Check arguments for self.enable_tools and disable_tools
        if self.enable_tools is not None:
            if not isinstance(self.enable_tools, list) or any(
                not isinstance(_, str) for _ in self.enable_tools
            ):
                raise ValueError(
                    "Enable tools should be a list of strings, but got "
                    f"{self.enable_tools}.",
                )

        if self.disable_tools is not None:
            if not isinstance(self.disable_tools, list) or any(
                not isinstance(_, str) for _ in self.disable_tools
            ):
                raise ValueError(
                    "Disable tools should be a list of strings, but got "
                    f"{self.disable_tools}.",
                )

        if self.enable_tools is not None and self.disable_tools is not None:
            intersection = set(self.enable_tools).intersection(
                set(self.disable_tools),
            )
            if len(intersection) != 0:
                raise ValueError(
                    f"The tools in enable_tools and disable_tools "
                    f"should not overlap, but got {intersection}.",
                )

    def _create_stdio_transport(
        self,
    ) -> AbstractAsyncContextManager[Any]:
        """Build a fresh stdio transport."""
        assert isinstance(self.mcp_config, StdioMCPConfig)
        config = self.mcp_config
        return stdio_client(
            StdioServerParameters(
                command=config.command,
                args=config.args or [],
                env=config.env,
                cwd=str(config.cwd) if config.cwd else None,
                encoding="utf-8",
                encoding_error_handler=config.encoding_error_handler,
            ),
        )

    def _create_sdk_client(self) -> SDKClient:
        """Build an MCP 2.x client with automatic protocol negotiation."""
        if self.mcp_config.type == "stdio_mcp":
            transport = self._create_stdio_transport()
        else:
            transport = self._create_http_client()
        return SDKClient(
            transport,
            mode="auto",
        )

    def _create_http_client(
        self,
    ) -> AbstractAsyncContextManager[Any]:
        """Create an HTTP MCP transport (SSE or Streamable HTTP)."""
        config = self.mcp_config
        if self._is_sse:
            return sse_client(
                url=config.url,
                headers=config.headers,
                timeout=config.timeout,
            )

        return self._create_streamable_http_client()

    @property
    def _is_sse(self) -> bool:
        """Whether the configured URL points at the SSE transport. Only
        the path is inspected, so a query string (``/sse?key=...``) still
        resolves to SSE rather than falling through to streamable HTTP.
        """
        path = urlsplit(self.mcp_config.url).path
        return path.endswith("/sse") or path.endswith("/messages/")

    @asynccontextmanager
    async def _create_streamable_http_client(
        self,
    ) -> AsyncGenerator[Any, None]:
        """Create an owned HTTP client that runtime headers can update."""
        config = self.mcp_config
        # Use the SDK factory to keep the transport's own client defaults
        client = create_mcp_http_client(
            headers=config.headers,
            timeout=(
                httpx2.Timeout(config.timeout)
                if config.timeout is not None
                else None
            ),
        )
        # Snapshot before overlaying: clearing runtime headers restores it.
        self._static_headers = httpx2.Headers(client.headers)
        client.headers.update(self._runtime_headers)
        self._http_client = client

        try:
            async with client:
                async with streamable_http_client(
                    url=config.url,
                    http_client=client,
                ) as transport:
                    yield transport
        finally:
            if self._http_client is client:
                self._http_client = None

    async def set_runtime_headers(
        self,
        headers: dict[str, str],
    ) -> None:
        """Replace the headers sent with subsequent HTTP requests.

        The map is replaced rather than merged: an empty map drops all
        runtime overrides, so the static headers from :attr:`mcp_config`
        apply again. Runtime headers are live instance state, excluded
        from ``model_dump`` and workspace persistence.

        The update reaches the next outbound request without reconnecting.
        Two things it cannot reach: a call already under way, which keeps
        the snapshot it started with, and the long-lived GET stream of a
        Streamable HTTP session, whose headers are fixed when the stream
        is established. Headers MCP sends itself (``mcp-session-id``,
        ``content-type``, ...) are set per request and always win over
        the ones set here; the few httpx2 derives from the URL and body
        are rejected outright.

        Args:
            headers (`dict[str, str]`):
                The complete runtime header map. An empty dict clears it.

        Raises:
            `ValueError`:
                The client is not Streamable HTTP, or a header is
                invalid or owned by the HTTP layer.
        """
        if self.mcp_config.type != "http_mcp" or self._is_sse:
            raise ValueError(
                "Runtime headers require a Streamable HTTP MCP client.",
            )
        if not isinstance(headers, dict):
            raise ValueError("Runtime headers must be a dict.")

        # httpx2 accepts illegal names and CRLF in values, and only h11
        # rejects them mid-request, so validate before storing.
        for name, value in headers.items():
            if (
                not isinstance(name, str)
                or not isinstance(value, str)
                or not self._HEADER_NAME.fullmatch(name)
                or not self._HEADER_VALUE.fullmatch(value)
            ):
                raise ValueError(f"Runtime header {name!r} is invalid.")
            if name.lower() in self._RESERVED_HEADERS:
                raise ValueError(
                    f"Runtime header {name!r} is owned by the HTTP layer.",
                )

        self._runtime_headers = dict(headers)
        if self._http_client is not None:
            merged = httpx2.Headers(self._static_headers)
            merged.update(self._runtime_headers)
            self._http_client.headers = merged

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

        client = self._create_sdk_client()
        ready = asyncio.Event()
        close_requested = asyncio.Event()
        startup_result: list[SDKClient | BaseException] = []
        lifecycle_task = asyncio.create_task(
            self._run_client_lifecycle(
                client,
                ready,
                close_requested,
                startup_result,
            ),
        )
        self._lifecycle_task = lifecycle_task
        self._close_requested = close_requested

        try:
            await asyncio.shield(ready.wait())
            result = startup_result[0]
            if isinstance(result, BaseException):
                raise result
            self._client = result
            self._is_connected = True
            logger.info("MCP connected: %s", self.name)
        except BaseException:
            if not lifecycle_task.done():
                lifecycle_task.cancel()
            try:
                await asyncio.shield(lifecycle_task)
            except BaseException:
                pass
            self._client = None
            self._lifecycle_task = None
            self._close_requested = None
            self._is_connected = False
            raise

    @staticmethod
    async def _run_client_lifecycle(
        client: SDKClient,
        ready: asyncio.Event,
        close_requested: asyncio.Event,
        startup_result: list[SDKClient | BaseException],
    ) -> None:
        """Own one stateful client context from initialization to close."""
        try:
            async with client as connected_client:
                startup_result.append(connected_client)
                ready.set()
                await close_requested.wait()
        except BaseException as error:
            if not ready.is_set():
                startup_result.append(error)
                ready.set()
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

        lifecycle_task = self._lifecycle_task
        close_requested = self._close_requested
        if lifecycle_task is None or close_requested is None:
            raise RuntimeError(
                f"MCP '{self.name}' lifecycle is not initialized.",
            )

        close_requested.set()
        try:
            await lifecycle_task
        except Exception as e:
            if not ignore_errors:
                raise e
            logger.warning(
                "Error closing MCP '%s': %s",
                self.name,
                str(e),
            )
        finally:
            self._client = None
            self._lifecycle_task = None
            self._close_requested = None
            self._is_connected = False
            logger.info("MCP closed: %s", self.name)

    def _get_client_gen(self) -> AbstractAsyncContextManager[Any]:
        """Build a fresh MCP SDK client for a stateless operation."""
        return self._create_sdk_client()

    async def list_raw_tools(self) -> list[mcp.types.Tool]:
        """List available tools from the MCP server in raw
        :class:`mcp.types.Tool` form, applying ``enable_tools`` and
        ``disable_tools`` filtering.

        The full (unfiltered) tool list is cached on ``_cached_tools`` so
        :meth:`get_tool` can resolve names that were filtered out as well.

        Returns:
            `list[mcp.types.Tool]`:
                Raw MCP tool descriptors after filtering.

        Raises:
            RuntimeError: If not connected (for stateful connections).
        """
        if not self.is_stateful:
            # Stateless: create a temporary auto-negotiating client.
            async with self._get_client_gen() as client:
                res = await client.list_tools(cache_mode="refresh")
                self._cached_tools = res.tools
        else:
            # Stateful: use the existing high-level client.
            self._validate_connection()
            res = await self._client.list_tools(cache_mode="refresh")
            self._cached_tools = res.tools

        available_tools: list = self._cached_tools
        if self.enable_tools is not None:
            available_tools = [
                tool
                for tool in available_tools
                if tool.name in self.enable_tools
            ]
        if self.disable_tools is not None:
            available_tools = [
                _ for _ in available_tools if _.name not in self.disable_tools
            ]
        return available_tools

    async def list_tools(self) -> list[ToolBase]:
        """List available tools from the MCP server as wrapped
        :class:`ToolBase` instances. If `enable_tools` and `disable_tools`
        are not `None` in the constructor, the returned tools will be
        filtered accordingly.

        Returns:
            `list[ToolBase]`:
                List of available MCP tools.

        Raises:
            RuntimeError: If not connected (for stateful connections).
        """
        raw_tools = await self.list_raw_tools()
        return [await self.get_tool(_.name) for _ in raw_tools]

    async def _get_raw_tool(self, name: str) -> mcp.types.Tool:
        """Get one raw MCP tool descriptor by its upstream name.

        This entry point intentionally does not import ``agentscope.tool``.
        It is used by the standalone workspace gateway, whose minimal virtual
        environment contains only the MCP transport dependencies.

        Args:
            name (`str`):
                The upstream MCP tool name.

        Returns:
            `mcp.types.Tool`:
                The matching raw MCP tool descriptor.

        Raises:
            `ValueError`:
                No tool with the requested name exists.
            `RuntimeError`:
                The stateful MCP client is not connected.
        """
        if self._cached_tools is None:
            await self.list_raw_tools()

        for tool in self._cached_tools or []:
            if tool.name == name:
                return tool

        raise ValueError(
            f"Tool {name!r} not found in MCP server {self.name!r}",
        )

    async def _call_raw_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        progress_callback: Any | None = None,
    ) -> mcp.types.CallToolResult:
        """Call an MCP tool without constructing an AgentScope tool adapter.

        Internal contract shared with the standalone workspace gateway.
        GatewayMCPClient overrides this method to reject host-side transport
        execution. Changes must keep both gateway callers and that override
        in sync; this is not a public tool invocation API.

        Args:
            name (`str`):
                The upstream MCP tool name.
            arguments (`dict[str, Any] | None`):
                Arguments forwarded to the MCP server.
            progress_callback (`Any | None`):
                Optional MCP SDK progress callback.

        Returns:
            `mcp.types.CallToolResult`:
                The raw MCP SDK result.
        """
        await self._get_raw_tool(name)
        if not self.is_stateful:
            async with self._get_client_gen() as client:
                return await client.call_tool(
                    name,
                    arguments=arguments,
                    read_timeout_seconds=self.execution_timeout,
                    progress_callback=progress_callback,
                )

        self._validate_connection()
        return await self._client.call_tool(
            name,
            arguments=arguments,
            read_timeout_seconds=self.execution_timeout,
            progress_callback=progress_callback,
        )

    async def get_tool(
        self,
        name: str,
    ) -> MCPTool:
        """Get a tool by name from the MCP server.

        The returned MCPTool object implements ToolProtocol and can be:
        - Streamed directly: `async for chunk in await tool(arg1=val1)`
        - Called for its final result: `await tool.call_final(arg1=val1)`
        - Registered to toolkit: `toolkit.register_tool(tool)`

        Args:
            name: The name of the tool function to get.

        Returns:
            A tool object that implements ToolProtocol.

        Raises:
            ValueError: If the tool is not found.
            RuntimeError: If not connected (for stateful connections).
        """
        # Avoid circular import by importing here
        from ..tool import MCPTool

        target_tool = await self._get_raw_tool(name)

        # Create MCPTool based on stateful/stateless
        if not self.is_stateful:
            # Stateless: pass client generator
            return MCPTool(
                mcp_name=self.name,
                tool=target_tool,
                client_gen=self._get_client_gen,
                timeout=self.execution_timeout,
            )
        else:
            # Stateful: pass session
            self._validate_connection()
            return MCPTool(
                mcp_name=self.name,
                tool=target_tool,
                session=self._client,
                timeout=self.execution_timeout,
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
        if not self._client:
            raise RuntimeError(
                f"MCP '{self.name}' client is not initialized. "
                "Call connect() first.",
            )
