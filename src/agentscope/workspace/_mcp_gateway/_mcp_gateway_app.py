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

Config schema (new per-agent format)::

    {
        "token": "bearer-token",
        "servers": {
            "agent-leader": [<MCPClient.model_dump()>, ...],
            "agent-worker": [<MCPClient.model_dump()>, ...]
        }
    }

An old-style flat ``"servers": [...]`` list is still accepted and
auto-migrated under ``"_default"``.
"""

import argparse
import asyncio
import json
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from agentscope.mcp import MCPClient


# ── gateway state ──────────────────────────────────────────────────


class _State:
    """Mutable runtime state shared by FastAPI routes."""

    def __init__(self) -> None:
        self.clients: dict[str, dict[str, MCPClient]] = {}
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


async def _build_client(spec: dict[str, Any]) -> MCPClient:
    """Validate a config / request body into an :class:`MCPClient`,
    then connect if stateful so subsequent ``list_raw_tools`` /
    ``get_tool`` work without re-spawning the upstream session.
    """
    client = MCPClient.model_validate(spec)
    if client.is_stateful:
        await client.connect()
    # Prime the tool cache so /mcps/{name}/tools is cheap and stable.
    await client.list_raw_tools()
    return client


# ── FastAPI app ────────────────────────────────────────────────────


def _build_app(state: _State) -> FastAPI:
    """Build the FastAPI app with all routes wired against ``state``."""
    app = FastAPI(title="agentscope-workspace-mcp-gateway")
    auth = Depends(_make_auth_dep(state))

    @app.get("/health")
    async def _health() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/mcps", dependencies=[auth])
    async def _list_mcps(agent_id: str) -> list[dict[str, Any]]:
        # Return the client specs for the requested agent only.
        return [
            clients[agent_id].model_dump(mode="json")
            for clients in state.clients.values()
            if agent_id in clients
        ]

    @app.post("/mcps", dependencies=[auth])
    async def _add_mcp(
        agent_id: str,
        request: Request,
    ) -> dict[str, Any]:
        body = await request.json()
        name = body.get("name", "")
        if not name:
            raise HTTPException(400, "name required")
        async with state.lock:
            by_agent = state.clients.setdefault(name, {})
            if agent_id in by_agent:
                raise HTTPException(
                    409,
                    f"{name!r} already exists for agent {agent_id!r}",
                )
            try:
                client = await _build_client(body)
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                raise HTTPException(
                    500,
                    f"connect failed: {e}",
                ) from e
            by_agent[agent_id] = client
        return {"ok": True}

    @app.delete("/mcps/{name}", dependencies=[auth])
    async def _remove_mcp(agent_id: str, name: str) -> dict[str, Any]:
        async with state.lock:
            by_agent = state.clients.get(name, {})
            client = by_agent.pop(agent_id, None)
            if client is None:
                raise HTTPException(
                    404,
                    f"{name!r} not found for agent {agent_id!r}",
                )
            if client.is_stateful and client.is_connected:
                await client.close()
        return {"ok": True}

    @app.get("/mcps/{name}/tools", dependencies=[auth])
    async def _list_tools(agent_id: str, name: str) -> list[dict[str, Any]]:
        by_agent = state.clients.get(name, {})
        client = by_agent.get(agent_id)
        if client is None:
            raise HTTPException(
                404,
                f"{name!r} not found for agent {agent_id!r}",
            )
        raw = await client.list_raw_tools()
        return [t.model_dump(mode="json") for t in raw]

    @app.post("/mcps/{name}/tools/{tool}", dependencies=[auth])
    async def _call_tool(
        agent_id: str,
        name: str,
        tool: str,
        request: Request,
    ) -> dict[str, Any]:
        by_agent = state.clients.get(name, {})
        client = by_agent.get(agent_id)
        if client is None:
            raise HTTPException(
                404,
                f"{name!r} not found for agent {agent_id!r}",
            )
        body = await request.json()
        arguments = body.get("arguments") or {}
        try:
            tool_obj = await client.get_tool(tool)
            chunk = await tool_obj(**arguments)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, str(e)) from e
        return {"chunk": chunk.model_dump(mode="json")}

    return app


# ── lifecycle ──────────────────────────────────────────────────────


async def _connect_initial(
    state: _State,
    server_cfgs: list[dict[str, Any]] | dict[str, list[dict[str, Any]]],
) -> None:
    """Connect every server listed in the static config file.

    Supports both old and new config formats:

    * Old: ``[config, ...]`` (flat list; migrated under ``"_default"``).
    * New: ``{"agent_id": [config, ...], ...}`` (per-agent).
    """
    if isinstance(server_cfgs, list):
        # Old flat-list format — auto-migrate
        for cfg in server_cfgs:
            client = await _build_client(cfg)
            state.clients.setdefault(client.name, {})["_default"] = client
            print(
                f"[gateway] connected {client.name!r} (agent _default)",
                flush=True,
            )
    else:
        for agent_id, cfgs in server_cfgs.items():
            for cfg in cfgs:
                client = await _build_client(cfg)
                state.clients.setdefault(client.name, {})[agent_id] = client
                print(
                    f"[gateway] connected {client.name!r} "
                    f"(agent {agent_id})",
                    flush=True,
                )


async def _run(config_path: str, port: int) -> None:
    """Read config, connect upstreams, start uvicorn, clean up on exit."""
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    state = _State()
    state.token = config.get("token", "") or ""
    await _connect_initial(state, config.get("servers", []) or [])

    app = _build_app(state)
    print(
        f"[gateway] serving {len(state.clients)} MCPs on :{port}",
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
        for by_agent in state.clients.values():
            for client in by_agent.values():
                if client.is_stateful and client.is_connected:
                    await client.close()


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
