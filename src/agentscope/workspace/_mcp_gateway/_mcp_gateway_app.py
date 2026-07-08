# -*- coding: utf-8 -*-
"""In-workspace MCP gateway — FastAPI router over agentscope MCPClients.

Runs inside the workspace environment as a standalone script. Reads
``--config`` — a JSON list of ``MCPClient.model_dump()`` dicts (same
format as the workspace's ``.mcp`` file) — instantiates one client per
entry, and exposes per-server HTTP endpoints. No auth: the gateway is
only reachable via ``backend.exec_shell`` from inside the sandbox.

Endpoints::

    GET    /health
    GET    /mcps                                # [MCPClient.model_dump(), ...]
    POST   /mcps                                # body: MCPClient.model_dump()
    DELETE /mcps/{name}
    GET    /mcps/{name}/tools
    POST   /mcps/{name}/tools/{tool}            # body: {arguments: {...}}

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
        self.clients: dict[str, MCPClient] = {}
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
    async def _list_mcps() -> list[dict[str, Any]]:
        return [c.model_dump(mode="json") for c in state.clients.values()]

    @app.post("/mcps")
    async def _add_mcp(request: Request) -> dict[str, Any]:
        body = await request.json()
        name = body.get("name", "")
        if not name:
            raise HTTPException(400, "name required")
        async with state.lock:
            if name in state.clients:
                raise HTTPException(409, f"{name!r} already exists")
            try:
                client = await _build_client(body)
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                raise HTTPException(500, f"connect failed: {e}") from e
            state.clients[name] = client
        return {"ok": True}

    @app.delete("/mcps/{name}")
    async def _remove_mcp(name: str) -> dict[str, Any]:
        async with state.lock:
            client = state.clients.pop(name, None)
            if client is None:
                raise HTTPException(404, f"{name!r} not found")
            if client.is_stateful and client.is_connected:
                await client.close()
        return {"ok": True}

    @app.get("/mcps/{name}/tools")
    async def _list_tools(name: str) -> list[dict[str, Any]]:
        client = state.clients.get(name)
        if client is None:
            raise HTTPException(404, f"{name!r} not found")
        raw = await client.list_raw_tools()
        return [t.model_dump(mode="json") for t in raw]

    @app.post("/mcps/{name}/tools/{tool}")
    async def _call_tool(
        name: str,
        tool: str,
        request: Request,
    ) -> dict[str, Any]:
        client = state.clients.get(name)
        if client is None:
            raise HTTPException(404, f"{name!r} not found")
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
    server_cfgs: list[dict[str, Any]],
) -> None:
    """Connect every server listed in the config file."""
    for cfg in server_cfgs:
        client = await _build_client(cfg)
        if client.name in state.clients:
            if client.is_stateful and client.is_connected:
                await client.close()
            raise ValueError(
                f"Duplicated server name in config: {client.name!r}",
            )
        state.clients[client.name] = client
        print(f"[gateway] connected {client.name!r}", flush=True)


async def _run(config_path: str, port: int) -> None:
    """Read config, connect upstreams, start uvicorn, clean up on exit."""
    with open(config_path, encoding="utf-8") as f:
        servers = json.load(f)
    if not isinstance(servers, list):
        raise ValueError(
            f"config file must be a JSON list of MCPClient specs, "
            f"got {type(servers).__name__}",
        )

    state = _State()
    await _connect_initial(state, servers)

    app = _build_app(state)
    print(
        f"[gateway] serving {len(state.clients)} MCPs on :{port}",
        flush=True,
    )

    import uvicorn

    uvi_cfg = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(uvi_cfg)
    try:
        await server.serve()
    finally:
        for client in list(state.clients.values()):
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
