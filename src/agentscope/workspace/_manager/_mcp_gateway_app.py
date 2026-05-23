# -*- coding: utf-8 -*-
"""In-workspace MCP Gateway — runs *inside* the workspace environment.

A ``WorkspaceWithMCP`` subclass copies this file into its workspace
environment and executes it there.  The script reads a JSON config,
connects to all configured MCP servers (stdio or HTTP), aggregates
their tools, and exposes a single Streamable-HTTP MCP endpoint that
the host can reach.

Also exposes ``/mcp/add`` and ``/mcp/remove`` for dynamic
MCP server management.

Usage::

    python /tmp/_in_container_gateway.py \\
        --config /tmp/.gw_config.json --port 5600

Config JSON schema::

    {
        "token": "bearer-token",
        "servers": [
            {"name": "fs", "transport": "stdio",
             "command": "mcp-server-fs", "args": [], "env": {}},
            {"name": "web", "transport": "http",
             "url": "http://localhost:8080/mcp"}
        ]
    }
"""

import argparse
import asyncio
import json
import keyword
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

import mcp.types as mtypes
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP

TOOL_NAME_SEPARATOR = "___"


# ── MCP server client ─────────────────────────────────────────────


class _MCPServerClient:
    """Persistent connection to one MCP server inside the workspace.

    This intentionally duplicates a subset of ``agentscope.mcp.MCPClient``
    because this script is copied into the workspace as a standalone file
    and ``agentscope`` is not installed there.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.session: ClientSession | None = None
        self.tools: list[mtypes.Tool] = []
        self._stack: AsyncExitStack | None = None

    async def connect_stdio(  # noqa: D401
        self,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        """Connect to an MCP server via stdio transport."""
        self._stack = AsyncExitStack()
        ctx = stdio_client(
            StdioServerParameters(command=command, args=args, env=env),
        )
        streams = await self._stack.enter_async_context(ctx)
        self.session = ClientSession(streams[0], streams[1])
        await self._stack.enter_async_context(self.session)
        await self.session.initialize()
        self.tools = (await self.session.list_tools()).tools

    async def connect_http(self, url: str) -> None:
        """Connect to an MCP server via HTTP transport."""
        self._stack = AsyncExitStack()
        if url.endswith("/sse") or url.endswith("/messages/"):
            ctx = sse_client(url=url)
        else:
            ctx = streamable_http_client(url=url)
        streams = await self._stack.enter_async_context(ctx)
        self.session = ClientSession(streams[0], streams[1])
        await self._stack.enter_async_context(self.session)
        await self.session.initialize()
        self.tools = (await self.session.list_tools()).tools

    async def close(self) -> None:
        """Close the connection and release resources."""
        if self._stack:
            try:
                await self._stack.aclose()
            except Exception:
                pass
            self._stack = None
            self.session = None


# ── route table ───────────────────────────────────────────────────


class _ToolRoute:
    """Maps an exposed tool name to the upstream MCP server client.

    Attributes:
        client: The MCP server client that owns this tool.
        original_name: The tool's original name as registered on the
            upstream MCP server (before any gateway-level renaming).
    """

    def __init__(self, client: _MCPServerClient, original_name: str) -> None:
        self.client = client
        #: The tool's upstream name before gateway-level prefixing.
        #: When the host calls ``server___tool``, the gateway uses
        #: ``original_name`` (i.e. just ``tool``) to invoke the real
        #: tool on the upstream MCP server via ``session.call_tool``.
        self.original_name = original_name


# ── gateway state (mutable at runtime) ────────────────────────────


class _GatewayState:
    """Shared mutable state for the gateway; mutated by admin endpoints."""

    def __init__(self) -> None:
        self.clients: list[_MCPServerClient] = []
        self.routes: dict[str, _ToolRoute] = {}
        self.schemas: dict[str, mtypes.Tool] = {}
        self.server: FastMCP | None = None

    def rebuild(self) -> None:
        """Rebuild routes and tool schemas from all connected clients.

        All exposed tool names use the format
        ``server_name___tool_name`` to ensure a consistent naming
        convention regardless of conflicts.
        """
        routes: dict[str, _ToolRoute] = {}
        schemas: dict[str, mtypes.Tool] = {}
        for c in self.clients:
            for t in c.tools:
                exposed = f"{c.name}{TOOL_NAME_SEPARATOR}{t.name}"
                routes[exposed] = _ToolRoute(c, t.name)
                schemas[exposed] = t
        self.routes = routes
        self.schemas = schemas


_state = _GatewayState()


# ── main ──────────────────────────────────────────────────────────


async def _run(config_path: str, port: int) -> None:
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    token: str = config.get("token", "")
    server_cfgs: list[dict[str, Any]] = config.get("servers", [])

    for cfg in server_cfgs:
        c = _MCPServerClient(cfg["name"])
        transport = cfg.get("transport", "stdio")
        if transport == "http":
            await c.connect_http(cfg["url"])
        else:
            await c.connect_stdio(
                cfg["command"],
                cfg.get("args", []),
                cfg.get("env"),
            )
        _state.clients.append(c)
        print(
            f"[gateway] connected to {c.name!r} ({len(c.tools)} tools)",
            flush=True,
        )

    _state.rebuild()

    server = FastMCP("workspace-gateway")
    _state.server = server

    _register_proxy_tools(server, _state)

    from starlette.requests import Request
    from starlette.responses import JSONResponse, PlainTextResponse
    from starlette.routing import Route

    app = server.streamable_http_app()

    if token:
        from starlette.middleware.base import BaseHTTPMiddleware

        class _TokenAuth(BaseHTTPMiddleware):
            async def dispatch(  # noqa: D401
                self,
                request: Request,
                call_next: Any,
            ) -> Any:
                """Enforce bearer-token auth on non-health endpoints."""
                path = request.url.path
                if path == "/health":
                    return await call_next(request)
                auth = request.headers.get("authorization", "")
                if auth != f"Bearer {token}":
                    return JSONResponse(
                        {"error": "unauthorized"},
                        status_code=401,
                    )
                return await call_next(request)

        app.add_middleware(_TokenAuth)

    async def _health(
        request: Request,  # pylint: disable=unused-argument
    ) -> PlainTextResponse:
        """Liveness probe endpoint."""
        return PlainTextResponse("ok")

    async def _add_mcp(request: Request) -> JSONResponse:
        body = await request.json()
        name = body.get("name", "")
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        for c in _state.clients:
            if c.name == name:
                return JSONResponse(
                    {"error": f"{name!r} already exists"},
                    status_code=409,
                )
        c = _MCPServerClient(name)
        transport = body.get("transport", "stdio")
        try:
            if transport == "http":
                await c.connect_http(body["url"])
            else:
                await c.connect_stdio(
                    body["command"],
                    body.get("args", []),
                    body.get("env"),
                )
        except Exception as e:
            return JSONResponse(
                {"error": f"connect failed: {e}"},
                status_code=500,
            )
        _state.clients.append(c)
        _state.rebuild()
        _register_proxy_tools(server, _state)
        return JSONResponse(
            {"ok": True, "tools": len(c.tools)},
        )

    async def _remove_mcp(request: Request) -> JSONResponse:
        body = await request.json()
        name = body.get("name", "")
        target: _MCPServerClient | None = None
        for c in _state.clients:
            if c.name == name:
                target = c
                break
        if target is None:
            return JSONResponse(
                {"error": f"{name!r} not found"},
                status_code=404,
            )
        _state.clients.remove(target)
        await target.close()
        _state.rebuild()
        _register_proxy_tools(server, _state)
        return JSONResponse({"ok": True})

    async def _list_mcp(
        request: Request,  # pylint: disable=unused-argument
    ) -> JSONResponse:
        """List connected MCP servers."""
        items = [
            {"name": c.name, "tools": len(c.tools)} for c in _state.clients
        ]
        return JSONResponse(items)

    async def _list_tools(
        request: Request,  # pylint: disable=unused-argument
    ) -> JSONResponse:
        """Return JSON list of all available tool schemas."""
        tools = []
        for name, schema in _state.schemas.items():
            tools.append(
                {
                    "name": name,
                    "description": schema.description or "",
                    "inputSchema": schema.inputSchema or {},
                },
            )
        return JSONResponse(tools)

    async def _call_tool(request: Request) -> JSONResponse:
        body = await request.json()
        tool_name = body.get("name", "")
        arguments = body.get("arguments", {})
        route = _state.routes.get(tool_name)
        if route is None:
            return JSONResponse(
                {"error": f"tool {tool_name!r} not found"},
                status_code=404,
            )
        if not route.client.session:
            return JSONResponse(
                {"error": f"upstream {route.client.name!r} not connected"},
                status_code=502,
            )
        try:
            result = await route.client.session.call_tool(
                route.original_name,
                arguments=arguments,
            )
            parts = []
            for c in result.content:
                if hasattr(c, "text"):
                    parts.append(c.text)
                else:
                    parts.append(str(c))
            return JSONResponse({"result": "\n".join(parts)})
        except Exception as exc:
            return JSONResponse(
                {"error": str(exc)},
                status_code=500,
            )

    app.routes.insert(0, Route("/health", _health))
    app.routes.insert(1, Route("/mcp/add", _add_mcp, methods=["POST"]))
    app.routes.insert(
        2,
        Route("/mcp/remove", _remove_mcp, methods=["POST"]),
    )
    app.routes.insert(3, Route("/mcp/list", _list_mcp))
    app.routes.insert(4, Route("/tools/list", _list_tools))
    app.routes.insert(
        5,
        Route("/tools/call", _call_tool, methods=["POST"]),
    )

    print(
        f"[gateway] serving {len(_state.routes)} tools on :{port}",
        flush=True,
    )

    import uvicorn

    uvi_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )
    uvi_server = uvicorn.Server(uvi_config)
    try:
        await uvi_server.serve()
    finally:
        for c in reversed(_state.clients):
            await c.close()


def _make_proxy(
    route: _ToolRoute,
    tool_schema: mtypes.Tool,
) -> Callable[..., Awaitable[str]]:
    """Build a proxy function whose parameter names match the upstream tool.

    FastMCP introspects the function signature to generate its pydantic
    argument model.  If we used ``**kwargs``, FastMCP would create a model
    with a single ``kwargs`` field, and callers sending ``{"name": "Docker"}``
    would get a "kwargs field required" validation error.

    Instead we dynamically create a function with named parameters that
    match the upstream tool's ``inputSchema.properties``.
    """
    input_schema = tool_schema.inputSchema or {}
    properties = input_schema.get("properties", {})

    # Build mapping: original_name -> safe_param_name
    # Python keywords (class, import, etc.) get a trailing underscore.
    name_map: dict[str, str] = {}
    for k in properties:
        if not k.isidentifier():
            continue
        if keyword.iskeyword(k):
            name_map[k] = k + "_"
        else:
            name_map[k] = k

    async def _do_call(arguments: dict) -> str:
        if not route.client.session:
            raise RuntimeError(
                f"upstream {route.client.name!r} not connected",
            )
        result = await route.client.session.call_tool(
            route.original_name,
            arguments=arguments,
        )
        parts = []
        for c in result.content:
            if hasattr(c, "text"):
                parts.append(c.text)
            else:
                parts.append(str(c))
        return "\n".join(parts)

    if name_map:
        sig = ", ".join(name_map.values())
        pack = (
            "{"
            + ", ".join(f"'{orig}': {safe}" for orig, safe in name_map.items())
            + "}"
        )
        body = f"async def proxy({sig}):\n    return await _do_call({pack})\n"
    else:
        body = "async def proxy():\n    return await _do_call({})\n"

    ns: dict[str, Any] = {"_do_call": _do_call}
    exec(body, ns)  # noqa: S102
    return ns["proxy"]


def _register_proxy_tools(
    server: FastMCP,
    state: _GatewayState,
) -> None:
    """(Re-)register proxy tool functions on the FastMCP server.

    Uses a **clear-and-rebuild** strategy: all previously registered
    tools are removed first, then only the tools present in
    ``state.schemas`` (which reflects the current set of connected
    clients) are re-registered.  This guarantees that tools belonging
    to a removed upstream server are no longer exposed.
    """
    # pylint: disable=protected-access
    server._tool_manager._tools.clear()
    for exposed_name, tool_schema in state.schemas.items():
        route = state.routes[exposed_name]
        proxy = _make_proxy(route, tool_schema)
        proxy.__name__ = exposed_name
        proxy.__doc__ = tool_schema.description or exposed_name

        server.tool(
            name=exposed_name,
            description=tool_schema.description,
        )(proxy)


def main() -> None:
    """CLI entry point for the in-workspace MCP gateway."""
    parser = argparse.ArgumentParser(
        description="In-workspace MCP Gateway",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=5600)
    args = parser.parse_args()
    asyncio.run(_run(args.config, args.port))


if __name__ == "__main__":
    main()
