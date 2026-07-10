# -*- coding: utf-8 -*-
"""In-workspace MCP gateway — FastAPI router over agentscope MCPClients.

Runs inside the workspace environment as a standalone script. Reads
``--config`` — a JSON list of ``MCPClient.model_dump()`` dicts (same
format as the workspace's ``.mcp`` file) — instantiates one client per
entry, and exposes per-server HTTP endpoints. No auth: the gateway is
only reachable via ``backend.exec_shell`` from inside the sandbox.

Endpoints::

    GET    /health
    GET    /mcps                       # [{name, tools}, ...]
    POST   /mcps                       # body: MCPClient.model_dump()
    DELETE /mcps/{name}
    GET    /mcps/{name}/tools
    POST   /mcps/{name}/tools/{tool}   # body: {arguments: {...}}

All endpoints (except ``/health``) accept ``?agent_id=`` to isolate
per-agent MCP sessions.

Config supports both old flat-list and new per-agent dict formats::

    # Old (flat list — auto-migrated under "_default")
    [<MCPClient.model_dump()>, ...]

    # New (per-agent dict)
    {"agent-leader": [<MCPClient.model_dump()>, ...], ...}

The absolute import for ``agentscope.mcp`` avoids loading
``agentscope.workspace.__init__`` (which pulls in skill/tool trees the
gateway does not need).
"""

import argparse
import asyncio
import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from agentscope.mcp import MCPClient


class _State:
    """Mutable runtime state shared by FastAPI routes."""

    def __init__(self) -> None:
        self.clients: dict[str, dict[str, MCPClient]] = {}
        self.lock = asyncio.Lock()


async def _build_client(spec: dict[str, Any]) -> MCPClient:
    """Validate a spec into an ``MCPClient``, connect if stateful,
    and prime its tool cache.
    """
    client = MCPClient.model_validate(spec)
    if client.is_stateful:
        await client.connect()
    await client.list_raw_tools()
    return client


def _build_app(state: _State) -> FastAPI:
    """Build the FastAPI app with all routes wired against ``state``."""
    app = FastAPI(title="agentscope-workspace-mcp-gateway")

    @app.get("/health")
    async def _health() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/mcps")
    async def _list_mcps(agent_id: str) -> list[dict[str, Any]]:
        # Return the client specs for the requested agent only.
        return [
            clients[agent_id].model_dump(mode="json")
            for clients in state.clients.values()
            if agent_id in clients
        ]

    @app.post("/mcps")
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
                raise HTTPException(500, f"connect failed: {e}") from e
            by_agent[agent_id] = client
        return {"ok": True}

    @app.delete("/mcps/{name}")
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

    @app.get("/mcps/{name}/tools")
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

    @app.post("/mcps/{name}/tools/{tool}")
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


async def _connect_initial(
    state: _State,
    server_cfgs: list[dict[str, Any]] | dict[str, list[dict[str, Any]]],
) -> None:
    """Connect every server listed in the config file.

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
    # Support both new per-agent dict and old flat list format.
    if isinstance(config, dict):
        servers = config.get("servers", [])
    else:
        servers = config
    if not isinstance(servers, (list, dict)):
        raise ValueError(
            f"config 'servers' must be a JSON list or per-agent dict, "
            f"got {type(servers).__name__}",
        )
    await _connect_initial(state, servers or [])

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
        log_level="info",
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
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="In-workspace MCP gateway (FastAPI)",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--port", type=int, default=5600)
    args = parser.parse_args()
    asyncio.run(_run(args.config, args.port))


if __name__ == "__main__":
    main()
