# -*- coding: utf-8 -*-
"""In-workspace MCP gateway — FastAPI router over agentscope MCPClients.

Runs *inside* the workspace environment as a standalone script
(``python /path/to/_mcp_gateway_app.py``). Reads ``--config`` JSON,
instantiates one :class:`agentscope.mcp.MCPClient` per configured server,
and exposes per-server HTTP endpoints. Each call is forwarded to the
underlying ``MCPClient`` (which owns the upstream session).

The script uses an absolute import for ``agentscope.mcp`` (rather than
a package-relative import) so it can be invoked directly without
loading ``agentscope.workspace.__init__`` — the latter eagerly imports
heavy modules (skill, tool, …) that are unnecessary for the gateway
and would force their dependencies into the in-container venv.

Endpoints
---------

    GET    /health                              # liveness, no auth
    GET    /mcps                                # [{name, tools}, ...]
    POST   /mcps                                # body: MCPClient.model_dump()
    DELETE /mcps/{name}
    GET    /mcps/{name}/tools                   # upstream tool schemas
    POST   /mcps/{name}/tools/{tool}            # body: {arguments: {...}}

Auth: every endpoint except ``/health`` requires
``Authorization: Bearer <token>`` when a token is configured.

Config schema::

    {
        "token": "bearer-token",
        "servers": [<MCPClient.model_dump()>, ...]
    }
"""

import argparse
import asyncio
import json
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from agentscope.mcp import MCPClient, MCPConnectionPool, MCPDefinition

# ── gateway state ──────────────────────────────────────────────────


class _State:
    """Mutable runtime state shared by FastAPI routes."""

    def __init__(self) -> None:
        self.definitions: dict[str, MCPDefinition] = {}
        self.pool = MCPConnectionPool()
        self.workspace_id = "gateway"
        self.token: str = ""
        self.lock = asyncio.Lock()


def _make_auth_dep(state: _State) -> Any:
    """Build a Bearer-token auth dependency closed over the state.

    No-op when ``state.token`` is empty.
    """

    async def _auth(request: Request) -> None:
        if not state.token:
            return
        header = request.headers.get("authorization", "")
        if header != f"Bearer {state.token}":
            raise HTTPException(status_code=401, detail="unauthorized")

    return _auth


# ── client construction ───────────────────────────────────────────


def _build_definition(
    state: _State,
    spec: dict[str, Any],
    revision: int = 1,
) -> MCPDefinition:
    """Validate configuration without opening an actor connection."""
    client = MCPClient.model_validate(spec)
    return MCPDefinition.from_client(
        workspace_id=state.workspace_id,
        client=client,
        definition_id=client.name,
        revision=revision,
    )


async def _validate_definition(
    state: _State,
    definition: MCPDefinition,
) -> None:
    """Probe tool discovery, then release the temporary validation scope."""
    provider = state.pool.provider(
        definition,
        agent_id="__gateway__",
        session_id="__validation__",
    )
    try:
        await provider.get_tools()
    finally:
        await state.pool.close_scope(
            workspace_id=state.workspace_id,
            agent_id="__gateway__",
            session_id="__validation__",
        )


# ── FastAPI app ────────────────────────────────────────────────────


def _build_app(state: _State) -> FastAPI:
    """Build the FastAPI app with all routes wired against ``state``."""
    app = FastAPI(title="agentscope-workspace-mcp-gateway")
    auth = Depends(_make_auth_dep(state))

    @app.get("/health")
    async def _health() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/mcps", dependencies=[auth])
    async def _list_mcps() -> list[dict[str, Any]]:
        # Dump the full MCPClient field set so the host can rebuild
        # `GatewayMCPClient.model_validate(spec)` losslessly.
        return [d.client_spec for d in state.definitions.values()]

    @app.post("/mcps", dependencies=[auth])
    async def _add_mcp(request: Request) -> dict[str, Any]:
        body = await request.json()
        name = body.get("name", "")
        if not name:
            raise HTTPException(400, "name required")
        async with state.lock:
            if name in state.definitions:
                raise HTTPException(409, f"{name!r} already exists")
            try:
                definition = _build_definition(state, body)
                await _validate_definition(state, definition)
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                raise HTTPException(
                    500,
                    f"connect failed: {e}",
                ) from e
            state.definitions[name] = definition
        return {"ok": True}

    @app.delete("/mcps/{name}", dependencies=[auth])
    async def _remove_mcp(name: str) -> dict[str, Any]:
        async with state.lock:
            definition = state.definitions.pop(name, None)
            if definition is None:
                raise HTTPException(404, f"{name!r} not found")
            await state.pool.close_definition(
                workspace_id=state.workspace_id,
                definition_id=definition.id,
            )
        return {"ok": True}

    @app.get("/mcps/{name}/tools", dependencies=[auth])
    async def _list_tools(
        name: str,
        agent_id: str = Query(default="legacy"),
        session_id: str = Query(default="legacy"),
    ) -> list[dict[str, Any]]:
        definition = state.definitions.get(name)
        if definition is None:
            raise HTTPException(404, f"{name!r} not found")
        key = state.pool.provider(
            definition,
            agent_id=agent_id,
            session_id=session_id,
        ).key
        async with state.pool.operation(definition, key) as client:
            raw = await client.list_raw_tools()
            return [t.model_dump(mode="json") for t in raw]

    @app.post("/mcps/{name}/tools/{tool}", dependencies=[auth])
    async def _call_tool(
        name: str,
        tool: str,
        request: Request,
        agent_id: str = Query(default="legacy"),
        session_id: str = Query(default="legacy"),
    ) -> dict[str, Any]:
        definition = state.definitions.get(name)
        if definition is None:
            raise HTTPException(404, f"{name!r} not found")
        body = await request.json()
        arguments = body.get("arguments") or {}
        try:
            provider = state.pool.provider(
                definition,
                agent_id=agent_id,
                session_id=session_id,
            )
            tool_obj = next(
                (
                    item
                    for item in await provider.get_tools()
                    if item.raw_name == tool
                ),
                None,
            )
            if tool_obj is None:
                raise ValueError(f"Tool {tool!r} not found")
            chunk = await tool_obj(**arguments)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, str(e)) from e
        # ToolChunk is a pydantic model — let host reconstruct it.
        return {"chunk": chunk.model_dump(mode="json")}

    return app


# ── lifecycle ──────────────────────────────────────────────────────


async def _connect_initial(
    state: _State,
    server_cfgs: list[dict[str, Any]],
) -> None:
    """Connect every server listed in the static config file."""
    for cfg in server_cfgs:
        definition = _build_definition(state, cfg)
        if definition.name in state.definitions:
            raise ValueError(
                f"Duplicated server name in config: {definition.name!r}",
            )
        state.definitions[definition.name] = definition
        await _validate_definition(state, definition)
        print(f"[gateway] registered {definition.name!r}", flush=True)


async def _run(config_path: str, port: int) -> None:
    """Read config, connect upstreams, start uvicorn, clean up on exit."""
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    state = _State()
    state.token = config.get("token", "") or ""
    state.workspace_id = config.get("workspace_id", "gateway")
    await _connect_initial(state, config.get("servers", []) or [])

    app = _build_app(state)
    print(
        f"[gateway] serving {len(state.definitions)} MCPs on :{port}",
        flush=True,
    )

    import uvicorn

    uvi_cfg = uvicorn.Config(
        app,
        host="0.0.0.0",  # noqa: S104 — gateway listens inside container
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(uvi_cfg)
    try:
        await server.serve()
    finally:
        await state.pool.close()


def main() -> None:
    """CLI entry point — invoked via
    ``python -m agentscope.workspace._mcp_gateway``.
    """
    parser = argparse.ArgumentParser(
        description="In-workspace MCP gateway (FastAPI)",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=5600)
    args = parser.parse_args()
    asyncio.run(_run(args.config, args.port))


if __name__ == "__main__":
    main()
