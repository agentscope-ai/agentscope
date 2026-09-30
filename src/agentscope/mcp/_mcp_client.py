# -*- coding: utf-8 -*-
"""Unified MCP client implementation for AgentScope."""
import asyncio
import re
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
    AsyncExitStack,
)
from typing import Any, AsyncGenerator, ClassVar, TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
import mcp.types
from mcp import ClientSession, stdio_client, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.streamable_http import (
    create_mcp_http_client,
    streamable_http_client,
)
from pydantic import ConfigDict, Field, BaseModel, PrivateAttr

from ._config import StdioMCPConfig, HttpMCPConfig
from ._oauth import build_token_auth, TokenAuth
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

    - Stateful: Requires explicit connect() and close(), maintains session
    - Stateless: No connect() needed, creates temporary session per call

    Private attributes:
    - _client: The underlying MCP client context manager
    - _session: The MCP ClientSession (for stateful connections only)
    - _stack: AsyncExitStack for managing connection lifecycle
    - _is_connected: Connection state flag
    - _cached_tools: Cached list of tools
    - _http_client: The live HTTP client, while one is open
    - _static_headers: Its headers before any runtime override
    - _runtime_headers: See :meth:`set_runtime_headers`
    - _auth: The HTTP authentication handler, when OAuth is configured

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

        # OAuth, refreshed automatically before the token expires
        client = MCPClient(
            name="feishu",
            is_stateful=False,
            mcp_config=HttpMCPConfig(
                url="https://example.com/mcp",
                oauth=OAuthClientConfig(
                    grant_type="refresh_token",
                    token_url="https://example.com/oauth/token",
                    client_id="cli_xxx",
                    client_secret="***",
                    refresh_token="***",
                ),
            ),
        )

    """

    # httpx.Auth and arbitrary callables are not pydantic-friendly types,
    # and token_provider accepts both.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # httpx derives these from the request URL and body with setdefault,
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

    token_provider: Any = Field(default=None, exclude=True)
    """A custom credential source for HTTP MCP servers, for providers the
    declarative ``mcp_config.oauth`` cannot describe. Either an async
    callable returning an :class:`~agentscope.mcp.OAuthToken` (or a bare
    token string), or an :class:`httpx.Auth` for full control of the
    exchange -- the MCP SDK's ``OAuthClientProvider``, for one. It is live
    instance state, excluded from ``model_dump`` and workspace
    persistence, and cannot be combined with ``mcp_config.oauth``."""

    # Private attributes
    _client: Any = PrivateAttr(default=None)
    _session: ClientSession | None = PrivateAttr(default=None)
    _stack: AsyncExitStack | None = PrivateAttr(default=None)
    _is_connected: bool = PrivateAttr(default=False)
    _cached_tools: list[mcp.types.Tool] | None = PrivateAttr(default=None)
    _http_client: httpx.AsyncClient | None = PrivateAttr(default=None)
    _static_headers: httpx.Headers | None = PrivateAttr(default=None)
    _runtime_headers: dict[str, str] = PrivateAttr(default_factory=dict)
    _auth: httpx.Auth | None = PrivateAttr(default=None)

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

        # The credential source, from the explicit provider or the
        # declarative OAuth config. STDIO speaks no HTTP, so neither
        # applies there.
        if self.mcp_config.type == "stdio_mcp":
            if self.token_provider is not None:
                raise ValueError(
                    "token_provider requires an HTTP MCP client.",
                )
        else:
            self._auth = build_token_auth(
                self.token_provider,
                self.mcp_config.oauth,
            )

        # Initialize the underlying client
        self._initialize_client()

    def _initialize_client(self) -> None:
        """Pre-build the stdio client context manager."""
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
    ) -> AbstractAsyncContextManager[Any]:
        """Create an HTTP MCP client (SSE or streamable HTTP)."""
        config = self.mcp_config
        if self._is_sse:
            return sse_client(
                url=config.url,
                headers=config.headers,
                timeout=config.timeout,
                auth=self._auth,
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
        if config.headers or config.timeout:
            client = httpx.AsyncClient(
                headers=config.headers,
                timeout=config.timeout,
                auth=self._auth,
            )
        else:
            client = create_mcp_http_client(auth=self._auth)
        # Snapshot before overlaying: clearing runtime headers restores it.
        self._static_headers = httpx.Headers(client.headers)
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

    @property
    def _auth_header_name(self) -> str | None:
        """The lower-cased header the configured credential is sent in.

        `None` when no credential is configured, or when it is a caller
        supplied :class:`httpx.Auth` whose header is not ours to know.
        """
        if isinstance(self._auth, TokenAuth):
            return self._auth.header_name.lower()
        return None

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
        the ones set here; the few httpx derives from the URL and body
        are rejected outright. So is the header an OAuth credential is
        sent in, when one is configured: the auth handler rewrites it on
        every request, so setting it here would have no effect.

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

        # httpx accepts illegal names and CRLF in values, and only h11
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
            if name.lower() == self._auth_header_name:
                raise ValueError(
                    f"Runtime header {name!r} is owned by the configured "
                    "OAuth credential, which rewrites it on every request.",
                )

        self._runtime_headers = dict(headers)
        if self._http_client is not None:
            merged = httpx.Headers(self._static_headers)
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

        # Transports are one-shot context managers. Recreate them before every
        # connection so connect() -> close() -> connect() starts a fresh one.
        if self._client is None:
            if self.mcp_config.type == "http_mcp":
                self._client = self._create_http_client()
            else:
                self._initialize_client()

        assert self._client is not None
        stack = AsyncExitStack()
        self._stack = stack

        try:
            context = await stack.enter_async_context(self._client)
            read_stream, write_stream = context[0], context[1]
            self._session = ClientSession(read_stream, write_stream)
            await stack.enter_async_context(self._session)
            await self._session.initialize()

            self._is_connected = True
            logger.info("MCP connected: %s", self.name)
        except BaseException:
            # asyncio.CancelledError inherits BaseException, so a cancelled
            # initialization must close every context entered so far. The
            # close is shielded because an anyio cancel scope (a cancelled
            # FastAPI request, for one) keeps cancelling every await inside
            # it, which would otherwise abandon a live stdio subprocess.
            try:
                await asyncio.shield(stack.aclose())
            finally:
                self._client = None
                self._stack = None
                self._session = None
                self._is_connected = False
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
            self._client = None
            self._stack = None
            self._session = None
            self._is_connected = False
            logger.info("MCP closed: %s", self.name)

    def _get_client_gen(self) -> AbstractAsyncContextManager[Any]:
        """Get client generator for stateless connections."""
        if self.mcp_config.type == "stdio_mcp":
            return self._client
        else:
            return self._create_http_client()

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
        else:
            # Stateful: use existing session
            self._validate_connection()
            res = await self._session.list_tools()
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

    async def get_tool(
        self,
        name: str,
    ) -> MCPTool:
        """Get a tool by name from the MCP server.

        The returned MCPTool object implements ToolProtocol and can be:
        - Called directly: `await tool(arg1=val1)`
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

        # Fetch tools if not cached. Use list_raw_tools() to avoid the
        # recursion list_tools() → get_tool() → list_tools().
        if self._cached_tools is None:
            await self.list_raw_tools()

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
                timeout=self.execution_timeout,
            )
        else:
            # Stateful: pass session
            self._validate_connection()
            return MCPTool(
                mcp_name=self.name,
                tool=target_tool,
                session=self._session,
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
        if not self._session:
            raise RuntimeError(
                f"MCP '{self.name}' session is not initialized. "
                "Call connect() first.",
            )
