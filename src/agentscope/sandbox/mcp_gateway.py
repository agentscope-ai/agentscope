# -*- coding: utf-8 -*-
"""MCPGateway — aggregates MCP servers behind one unified tool interface.

One Sandbox holds one MCPGateway (always enabled). The gateway:
  1. Starts each configured MCP server (stdio or HTTP).
  2. Aggregates all tools into a single namespace (conflicts prefixed).
  3. Routes ``call_tool`` to the owning MCP server.
  4. Supports dynamic add/remove of MCP servers at runtime.

Tool naming conflict resolution:
  - If ``read_file`` exists in both ``filesystem`` and ``browser`` servers,
    both are exposed as ``filesystem___read_file`` and ``browser___read_file``.
  - Unique names are kept as-is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import mcp.types as mtypes

from .._logging import logger
from ..mcp import HttpMCPConfig, MCPClient, StdioMCPConfig

if TYPE_CHECKING:
    from .config import MCPServerConfig


@dataclass(slots=True)
class MCPGatewayConfig:
    """Gateway settings (listen port merged into ``exposed_ports``).

    The gateway is always enabled — the ``enabled`` field is kept only for
    backward compatibility and is always treated as ``True``.
    """

    port: int = 5600
    mcp_name: str = "sandbox"


@dataclass(slots=True)
class _ToolRoute:
    """Maps an exposed tool name to the owning client and original name."""

    client: MCPClient
    mcp_name: str
    original_name: str
    tool: mtypes.Tool


class MCPGateway:
    """Aggregates multiple MCP servers behind ``list_tools`` / ``call_tool``.

    Always enabled — every Sandbox has a gateway. Supports both stdio and
    HTTP transports, and dynamic add/remove of MCP servers.

    Usage (managed by ``Sandbox``)::

        gw = MCPGateway(config)
        await gw.start(mcp_configs, cwd="/sandbox/root")
        tools = await gw.list_tools()
        result = await gw.call_tool("read_file", {"path": "a.txt"})

        # Dynamic management
        await gw.add_server(new_config, cwd="/sandbox/root")
        await gw.remove_server("server_name")

        await gw.close()
    """

    TOOL_NAME_SEPARATOR = "___"

    def __init__(self, config: MCPGatewayConfig) -> None:
        self._config = config
        self._clients: dict[str, MCPClient] = {}
        self._tool_routes: dict[str, _ToolRoute] = {}
        self._is_started = False
        self._cwd: str | None = None

    @property
    def is_started(self) -> bool:
        """True after :meth:`start` finishes successfully."""
        return self._is_started

    # --- lifecycle ---

    async def start(
        self,
        mcp_configs: list[MCPServerConfig],
        *,
        cwd: str | None = None,
    ) -> None:
        """Connect MCP servers and build the routing table."""
        if self._is_started:
            return

        self._cwd = cwd

        for cfg in mcp_configs:
            await self._connect_server(cfg)

        self._rebuild_tool_routes()
        self._is_started = True
        logger.info(
            "MCPGateway: started with %d aggregated tools from %d servers",
            len(self._tool_routes),
            len(self._clients),
        )

    async def close(self) -> None:
        """Close all MCP clients."""
        names = list(self._clients.keys())
        for name in reversed(names):
            client = self._clients.pop(name)
            try:
                await client.close()
            except Exception as e:
                logger.warning(
                    "MCPGateway: error closing %r: %s",
                    name,
                    e,
                )
        self._tool_routes.clear()
        self._is_started = False

    # --- dynamic add / remove ---

    async def add_server(
        self,
        mcp_config: MCPServerConfig,
        *,
        cwd: str | None = None,
    ) -> None:
        """Add and connect a new MCP server at runtime.

        Args:
            mcp_config: Configuration for the MCP server to add.
            cwd: Working directory override (defaults to the cwd used at
                 gateway start).

        Raises:
            ValueError: If a server with the same name already exists.
        """
        if mcp_config.name in self._clients:
            raise ValueError(
                f"MCP server {mcp_config.name!r} already exists in gateway. "
                "Remove it first or use a different name.",
            )

        effective_cwd = cwd or self._cwd
        await self._connect_server(mcp_config, cwd_override=effective_cwd)
        self._rebuild_tool_routes()
        logger.info(
            "MCPGateway: dynamically added server %r (%d total tools)",
            mcp_config.name,
            len(self._tool_routes),
        )

    async def remove_server(self, name: str) -> None:
        """Remove and disconnect an MCP server at runtime.

        Args:
            name: Name of the server to remove.

        Raises:
            KeyError: If no server with the given name exists.
        """
        client = self._clients.pop(name, None)
        if client is None:
            raise KeyError(
                f"MCP server {name!r} not found in gateway. "
                f"Available: {list(self._clients.keys())}",
            )

        try:
            await client.close()
        except Exception as e:
            logger.warning(
                "MCPGateway: error closing removed server %r: %s",
                name,
                e,
            )

        self._rebuild_tool_routes()
        logger.info(
            "MCPGateway: removed server %r (%d tools remaining)",
            name,
            len(self._tool_routes),
        )

    # --- tool surface ---

    async def list_tools(
        self,
        *,
        mcp_names: list[str] | None = None,
    ) -> list[mtypes.Tool]:
        """Return tools; filter by ``mcp_names`` when given."""
        if mcp_names is None:
            return [r.tool for r in self._tool_routes.values()]
        name_set = set(mcp_names)
        return [
            r.tool
            for r in self._tool_routes.values()
            if r.mcp_name in name_set
        ]

    async def call_tool(
        self,
        name: str,
        args: dict[str, Any] | None = None,
    ) -> mtypes.CallToolResult:
        """Route a tool call to the owning MCP server."""
        route = self._tool_routes.get(name)
        if not route:
            available = list(self._tool_routes.keys())
            raise KeyError(
                f"Tool {name!r} not found in gateway. Available: {available}",
            )
        session = getattr(route.client, "_session", None)
        if not session:
            raise RuntimeError(
                f"MCP client {route.client.name!r} is not connected",
            )
        return await session.call_tool(
            route.original_name,
            arguments=args or {},
        )

    def has_tool(self, name: str) -> bool:
        """Return whether ``name`` exists in the routing table."""
        return name in self._tool_routes

    # --- info ---

    def list_servers(self) -> list[dict[str, Any]]:
        """Metadata rows for :meth:`Sandbox.list_mcps`."""
        return [
            {
                "name": c.name,
                "transport": c.mcp_config.type.replace("_mcp", ""),
                "connected": c.is_connected,
            }
            for c in self._clients.values()
        ]

    def get_mcp_name(self, exposed_tool_name: str) -> str | None:
        """Return the ``mcp_name`` that owns a tool, or ``None``."""
        route = self._tool_routes.get(exposed_tool_name)
        return route.mcp_name if route else None

    # --- internal helpers ---

    async def _connect_server(
        self,
        cfg: MCPServerConfig,
        *,
        cwd_override: str | None = None,
    ) -> None:
        """Create an MCPClient for a config and connect it."""
        cwd = cwd_override or self._cwd

        if cfg.transport == "http":
            mcp_config = HttpMCPConfig(
                url=cfg.url,
                headers=cfg.headers or None,
                timeout=cfg.timeout,
            )
            client = MCPClient(
                name=cfg.name,
                is_stateful=True,
                mcp_config=mcp_config,
            )
        else:
            # Default: stdio
            mcp_config = StdioMCPConfig(
                command=cfg.command,
                args=cfg.args or None,
                env=cfg.env or None,
                cwd=cwd,
            )
            client = MCPClient(
                name=cfg.name,
                is_stateful=True,
                mcp_config=mcp_config,
            )

        await client.connect()
        # Fetch tools immediately so _cached_tools is populated
        await client.list_tools()
        self._clients[cfg.name] = client
        logger.info(
            "MCPGateway: connected to %r (transport=%s, %d tools)",
            cfg.name,
            cfg.transport,
            len(getattr(client, "_cached_tools", None) or []),
        )

    def _rebuild_tool_routes(self) -> None:
        """Rebuild the full routing table from all connected clients."""
        raw: list[tuple[str, MCPClient, list[mtypes.Tool]]] = []
        for name, client in self._clients.items():
            cached = getattr(client, "_cached_tools", None)
            if cached is None:
                # Tools should have been fetched during connect; skip if empty
                cached = []
            raw.append((name, client, cached))
        self._tool_routes = self._build_tool_routes(raw)

    # --- route builder ---

    @staticmethod
    def _build_tool_routes(
        raw: list[tuple[str, MCPClient, list[mtypes.Tool]]],
    ) -> dict[str, _ToolRoute]:
        """Build the routing table with automatic conflict resolution.

        If two servers expose the same tool name, both are prefixed with
        ``{mcp_name}___`` to disambiguate.
        """
        sep = MCPGateway.TOOL_NAME_SEPARATOR

        name_count: dict[str, int] = {}
        for _mcp_name, _client, tools in raw:
            for tool in tools:
                name_count[tool.name] = name_count.get(tool.name, 0) + 1

        routes: dict[str, _ToolRoute] = {}
        for mcp_name, client, tools in raw:
            for tool in tools:
                if name_count[tool.name] > 1:
                    exposed = f"{mcp_name}{sep}{tool.name}"
                else:
                    exposed = tool.name

                exposed_tool = mtypes.Tool(
                    name=exposed,
                    description=tool.description,
                    inputSchema=tool.inputSchema,
                )
                routes[exposed] = _ToolRoute(
                    client=client,
                    mcp_name=mcp_name,
                    original_name=tool.name,
                    tool=exposed_tool,
                )
        return routes
