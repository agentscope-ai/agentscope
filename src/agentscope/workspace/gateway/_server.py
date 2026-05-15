# -*- coding: utf-8 -*-
"""In-container MCP Gateway — runs *inside* a Docker / E2B container.

Reads a JSON config, connects to all configured MCP servers
(stdio or HTTP) running inside the container, aggregates tools,
and exposes a single Streamable-HTTP MCP endpoint for the host.

Also exposes ``/admin/add`` and ``/admin/remove`` for dynamic
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
from contextlib import AsyncExitStack
from collections.abc import Awaitable, Callable
from typing import Any

from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
import mcp.types as mtypes

TOOL_NAME_SEPARATOR = "___"


# ── upstream MCP client ───────────────────────────────────────────


class _UpstreamClient:
    """Persistent connection to one MCP server inside the container."""

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
    __slots__ = ("client", "original_name")

    def __init__(self, client: _UpstreamClient, original_name: str) -> None:
        self.client = client
        self.original_name = original_name


def _build_routes(
    clients: list[_UpstreamClient],
) -> tuple[dict[str, _ToolRoute], dict[str, mtypes.Tool]]:
    """Build routes and tool schemas from connected clients."""
    name_count: dict[str, int] = {}
    for c in clients:
        for t in c.tools:
            name_count[t.name] = name_count.get(t.name, 0) + 1

    routes: dict[str, _ToolRoute] = {}
    schemas: dict[str, mtypes.Tool] = {}
    for c in clients:
        for t in c.tools:
            exposed = (
                f"{c.name}{TOOL_NAME_SEPARATOR}{t.name}"
                if name_count[t.name] > 1
                else t.name
            )
            routes[exposed] = _ToolRoute(c, t.name)
            schemas[exposed] = t
    return routes, schemas


# ── gateway state (mutable at runtime) ────────────────────────────


class _GatewayState:
    """Shared mutable state for the gateway; mutated by admin endpoints."""

    def __init__(self) -> None:
        self.clients: list[_UpstreamClient] = []
        self.routes: dict[str, _ToolRoute] = {}
        self.schemas: dict[str, mtypes.Tool] = {}
        self.server: FastMCP | None = None

    def rebuild(self) -> None:
        """Rebuild the tool route table from all connected clients."""
        self.routes, self.schemas = _build_routes(self.clients)


_state = _GatewayState()


# ── main ──────────────────────────────────────────────────────────


async def _run(config_path: str, port: int) -> None:
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    token: str = config.get("token", "")
    server_cfgs: list[dict[str, Any]] = config.get("servers", [])

    for cfg in server_cfgs:
        c = _UpstreamClient(cfg["name"])
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

    async def _admin_add(request: Request) -> JSONResponse:
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
        c = _UpstreamClient(name)
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

    async def _admin_remove(request: Request) -> JSONResponse:
        body = await request.json()
        name = body.get("name", "")
        target: _UpstreamClient | None = None
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

    async def _admin_list(
        request: Request,  # pylint: disable=unused-argument
    ) -> JSONResponse:
        """List connected upstream MCP servers."""
        items = [
            {"name": c.name, "tools": len(c.tools)} for c in _state.clients
        ]
        return JSONResponse(items)

    async def _api_tools(
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

    async def _api_call(request: Request) -> JSONResponse:
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
    app.routes.insert(1, Route("/admin/add", _admin_add, methods=["POST"]))
    app.routes.insert(
        2,
        Route("/admin/remove", _admin_remove, methods=["POST"]),
    )
    app.routes.insert(3, Route("/admin/list", _admin_list))
    app.routes.insert(4, Route("/api/tools", _api_tools))
    app.routes.insert(
        5,
        Route("/api/call", _api_call, methods=["POST"]),
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
    param_names = [k for k in properties if k.isidentifier()]

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

    if param_names:
        sig = ", ".join(param_names)
        pack = "{" + ", ".join(f"'{p}': {p}" for p in param_names) + "}"
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

    Clears previously registered tools first so that removed upstream
    servers no longer expose stale tools.
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
    """CLI entry point for the in-container MCP gateway."""
    parser = argparse.ArgumentParser(
        description="In-container MCP Gateway",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=5600)
    args = parser.parse_args()
    asyncio.run(_run(args.config, args.port))


if __name__ == "__main__":
    main()
