# -*- coding: utf-8 -*-
"""MCPGateway — aggregates MCP servers behind one unified tool interface.

One Sandbox holds one MCPGateway. The gateway:
  1. Starts each configured MCP server as a ``StdIOStatefulClient``.
  2. Aggregates all tools into a single namespace (conflicts prefixed).
  3. Routes ``call_tool`` to the owning MCP server.

Tool naming conflict resolution:
  - If ``read_file`` exists in both ``filesystem`` and ``browser`` servers,
    both are exposed as ``filesystem___read_file`` and ``browser___read_file``.
  - Unique names are kept as-is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import mcp.types as mtypes

from ..mcp._stdio_stateful_client import StdIOStatefulClient
from .._logging import logger

if TYPE_CHECKING:
    from .config import MCPServerConfig


@dataclass(slots=True)
class MCPGatewayConfig:
    """Gateway settings (listen port merged into ``exposed_ports``)."""

    enabled: bool = False
    port: int = 5600
    mcp_name: str = "sandbox"


@dataclass(slots=True)
class _ToolRoute:
    """Maps an exposed tool name to the owning client and original name."""

    client: StdIOStatefulClient
    mcp_name: str
    original_name: str
    tool: mtypes.Tool


class MCPGateway:
    """Aggregates multiple MCP servers behind ``list_tools`` / ``call_tool``.

    Usage (managed by ``Sandbox``)::

        gw = MCPGateway(config)
        await gw.start(mcp_configs, cwd="/sandbox/root")
        tools = await gw.list_tools()
        result = await gw.call_tool("read_file", {"path": "a.txt"})
        await gw.close()
    """

    TOOL_NAME_SEPARATOR = "___"

    def __init__(self, config: MCPGatewayConfig) -> None:
        self._config = config
        self._clients: list[StdIOStatefulClient] = []
        self._tool_routes: dict[str, _ToolRoute] = {}
        self._is_started = False

    @property
    def is_started(self) -> bool:
        """True after :meth:`start` finishes successfully."""
        return self._is_started

    # ─── lifecycle ────────────────────────────────────────────

    async def start(
        self,
        mcp_configs: list[MCPServerConfig],
        *,
        cwd: str | None = None,
    ) -> None:
        """Connect MCP servers and build the routing table."""
        if self._is_started:
            return

        raw: list[tuple[str, StdIOStatefulClient, list[mtypes.Tool]]] = []
        for cfg in mcp_configs:
            client = StdIOStatefulClient(
                name=cfg.name,
                command=cfg.command,
                args=cfg.args or [],
                env=cfg.env or None,
                cwd=cwd,
            )
            await client.connect()
            self._clients.append(client)
            tools = await client.list_tools()
            raw.append((cfg.name, client, tools))
            logger.info(
                "MCPGateway: connected to %r (%d tools)",
                cfg.name,
                len(tools),
            )

        self._tool_routes = self._build_tool_routes(raw)
        self._is_started = True
        logger.info(
            "MCPGateway: started with %d aggregated tools from %d servers",
            len(self._tool_routes),
            len(self._clients),
        )

    async def close(self) -> None:
        """Close MCP clients (LIFO)."""
        while self._clients:
            client = self._clients.pop()
            try:
                await client.close()
            except Exception as e:
                logger.warning(
                    "MCPGateway: error closing %r: %s",
                    client.name,
                    e,
                )
        self._tool_routes.clear()
        self._is_started = False

    # ─── tool surface ─────────────────────────────────────────

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
        if route is None:
            available = list(self._tool_routes.keys())
            raise KeyError(
                f"Tool {name!r} not found in gateway. Available: {available}",
            )
        return await route.client.session.call_tool(
            route.original_name,
            arguments=args or {},
        )

    def has_tool(self, name: str) -> bool:
        """Return whether ``name`` exists in the routing table.

        The name may be a conflict-disambiguated form like
        ``server___tool_name``.
        """
        return name in self._tool_routes

    # ─── info ─────────────────────────────────────────────────

    def list_servers(self) -> list[dict[str, Any]]:
        """Metadata rows for :meth:`Sandbox.list_mcps`."""
        return [
            {"name": c.name, "command": "stdio", "connected": c.is_connected}
            for c in self._clients
        ]

    def get_mcp_name(self, exposed_tool_name: str) -> str | None:
        """Return the ``mcp_name`` that owns a tool, or ``None``."""
        route = self._tool_routes.get(exposed_tool_name)
        return route.mcp_name if route else None

    # ─── route builder ────────────────────────────────────────

    @staticmethod
    def _build_tool_routes(
        raw: list[tuple[str, StdIOStatefulClient, list[mtypes.Tool]]],
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
