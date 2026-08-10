# -*- coding: utf-8 -*-
"""In-workspace MCP gateway — FastAPI router over agentscope MCPClients.

Runs inside the workspace environment as a standalone script. It starts
with an empty registry and never reads the workspace's ``.mcp`` file:
the workspace is the authority on which MCPs exist, and registers them
here on demand. Boot cost is therefore independent of how many agents
or sessions the workspace has accumulated. No auth: the gateway is only
reachable via ``backend.exec_shell`` from inside the sandbox.

Endpoints::

    GET    /health
    GET    /mcps                       # [MCPClient.model_dump(), ...]
    POST   /mcps                       # body: MCPClient.model_dump()
    DELETE /mcps/{name}
    GET    /mcps/{name}/tools
    POST   /mcps/{name}/tools/{tool}   # body: {arguments: {...}}

Every endpoint except ``/health`` takes ``?agent_id=&session_id=``.
Upstream sessions are keyed by ``(agent_id, session_id, name)``, so two
sessions running the same MCP get independent state (browser cookies,
login state) and one closing its client never disturbs the other.

The absolute import for ``agentscope.mcp`` avoids loading
``agentscope.workspace.__init__`` (which pulls in skill/tool trees the
gateway does not need).
"""

import argparse
import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from agentscope.mcp import MCPClient

_Scope = tuple[str, str]


class _State:
    """Mutable runtime state shared by FastAPI routes."""

    def __init__(self) -> None:
        self.clients: dict[_Scope, dict[str, MCPClient]] = {}
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

    def _lookup(scope: _Scope, name: str) -> MCPClient:
        """Resolve one registered client or raise 404."""
        client = state.clients.get(scope, {}).get(name)
        if client is None:
            raise HTTPException(
                404,
                f"{name!r} not found for agent={scope[0]!r} "
                f"session={scope[1]!r}",
            )
        return client

    @app.get("/health")
    async def _health() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/mcps")
    async def _list_mcps(
        agent_id: str = "",
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        return [
            c.model_dump(mode="json")
            for c in state.clients.get((agent_id, session_id), {}).values()
        ]

    @app.post("/mcps")
    async def _add_mcp(
        request: Request,
        agent_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        body = await request.json()
        name = body.get("name", "")
        if not name:
            raise HTTPException(400, "name required")
        scope = (agent_id, session_id)
        async with state.lock:
            by_name = state.clients.setdefault(scope, {})
            if name in by_name:
                raise HTTPException(
                    409,
                    f"{name!r} already exists for agent={agent_id!r} "
                    f"session={session_id!r}",
                )
            try:
                by_name[name] = await _build_client(body)
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                raise HTTPException(500, f"connect failed: {e}") from e
        return {"ok": True}

    @app.delete("/mcps/{name}")
    async def _remove_mcp(
        name: str,
        agent_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        scope = (agent_id, session_id)
        async with state.lock:
            client = _lookup(scope, name)
            del state.clients[scope][name]
            if not state.clients[scope]:
                del state.clients[scope]
            if client.is_stateful and client.is_connected:
                await client.close()
        return {"ok": True}

    @app.get("/mcps/{name}/tools")
    async def _list_tools(
        name: str,
        agent_id: str = "",
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        client = _lookup((agent_id, session_id), name)
        raw = await client.list_raw_tools()
        return [t.model_dump(mode="json") for t in raw]

    @app.post("/mcps/{name}/tools/{tool}")
    async def _call_tool(
        name: str,
        tool: str,
        request: Request,
        agent_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        client = _lookup((agent_id, session_id), name)
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


async def _run(port: int) -> None:
    """Start uvicorn on an empty registry, clean up upstreams on exit."""
    state = _State()
    app = _build_app(state)
    print(f"[gateway] serving on :{port}", flush=True)

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
        for by_name in state.clients.values():
            for client in by_name.values():
                if client.is_stateful and client.is_connected:
                    await client.close()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="In-workspace MCP gateway (FastAPI)",
    )
    # Accepted and ignored: the gateway no longer reads ``.mcp``.
    # Kept so a workspace image shipping an older launch command
    # still starts.
    parser.add_argument("--config", default=None)
    parser.add_argument("--port", type=int, default=5600)
    args = parser.parse_args()
    asyncio.run(_run(args.port))


if __name__ == "__main__":
    main()
