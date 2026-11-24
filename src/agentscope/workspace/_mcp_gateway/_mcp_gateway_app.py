# -*- coding: utf-8 -*-
"""In-workspace MCP gateway — FastAPI router over agentscope MCPClients.

Runs inside the workspace environment as a standalone script. It starts
with an empty registry and never reads the workspace's ``.mcp`` file:
the workspace is the authority on which MCPs exist, and registers them
here on demand. Boot cost is therefore independent of how many agents
or sessions the workspace has accumulated. Authentication is optional —
sandboxes sharing a host network namespace can enable a bearer token to
prevent cross-workspace gateway access.

Endpoints::

    GET    /health
    GET    /mcps                       # [MCPClient.model_dump(), ...]
    POST   /mcps                       # body: MCPClient.model_dump()
                                       #   (+ optional runtime_headers)
    DELETE /mcps/{name}
    PUT    /mcps/{name}/runtime-headers
    GET    /mcps/{name}/tools
    POST   /mcps/{name}/tools/{tool}   # body: {arguments: {...}}

Every endpoint except ``/health`` takes ``?agent_id=&session_id=``.
Upstream sessions are kept per agent, session and MCP name, so two
sessions running the same MCP get independent state (browser cookies,
login state) and one closing its client never disturbs the other.

The absolute import for ``agentscope.mcp`` avoids loading
``agentscope.workspace.__init__`` (which pulls in skill/tool trees the
gateway does not need).
"""

import argparse
import asyncio
import hashlib
import json
import secrets
from typing import Any

import anyio
import mcp.types
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from agentscope.mcp import MCPClient
from agentscope.tool import MCPTool


class _State:
    """Mutable runtime state shared by FastAPI routes."""

    def __init__(self) -> None:
        self.clients: dict[tuple[str, str], dict[str, MCPClient]] = {}
        self.registrations: dict[tuple[str, str, str], str] = {}
        self.lock = asyncio.Lock()


async def _build_client(
    spec: dict[str, Any],
    runtime_headers: dict[str, str] | None = None,
) -> MCPClient:
    """Validate a spec into an ``MCPClient``, connect if stateful,
    and prime its tool cache. Failed or cancelled discovery closes the
    acquired stateful connection before propagating the error.

    ``runtime_headers`` are applied before connecting: a rotated
    credential has to be in place for the handshake below, and it is
    not part of ``spec`` because it must never be persisted.
    """
    client = MCPClient.model_validate(spec)
    if runtime_headers:
        await client.set_runtime_headers(runtime_headers)
    if client.is_stateful:
        await client.connect()
    try:
        await client.list_raw_tools()
    except BaseException:
        # Until discovery succeeds the registry cannot close this client.
        if client.is_stateful and client.is_connected:
            with anyio.CancelScope(shield=True):
                await client.close()
        raise
    return client


def _build_app(
    state: _State,
    auth_token: str | None = None,
    instance_nonce: str | None = None,
) -> FastAPI:
    """Build the FastAPI app with all routes wired against ``state``."""
    if not callable(MCPTool.__dict__.get("bind_for_dispatch")):
        raise RuntimeError(
            "Gateway requires MCPTool bound dispatch. Rebuild the sandbox "
            "with the matching AgentScope runtime.",
        )
    app = FastAPI(title="agentscope-workspace-mcp-gateway")

    def _lookup(agent_id: str, session_id: str, name: str) -> MCPClient:
        """Resolve one registered client or raise 404."""
        client = state.clients.get((agent_id, session_id), {}).get(name)
        if client is None:
            raise HTTPException(
                404,
                f"{name!r} not found for agent={agent_id!r} "
                f"session={session_id!r}",
            )
        return client

    def _scope(agent_id: str, session_id: str, name: str) -> str:
        client = _lookup(agent_id, session_id, name)
        nonce = state.registrations[agent_id, session_id, name]
        # pylint: disable-next=protected-access
        return f"{nonce}.{client._runtime_headers_generation}"

    def _binding(scope: str, definition: mcp.types.Tool) -> str:
        raw = json.dumps(definition.model_dump(mode="json"), sort_keys=True)
        return f"{scope}.{hashlib.sha256(raw.encode()).hexdigest()}"

    if auth_token:

        @app.middleware("http")
        async def _auth_middleware(request: Request, call_next: Any) -> Any:
            if request.url.path == "/health":
                return await call_next(request)
            header = request.headers.get("authorization", "")
            expected = f"Bearer {auth_token}"
            valid = (
                header.isascii()
                and expected.isascii()
                and secrets.compare_digest(header, expected)
            )
            if not valid:
                return PlainTextResponse(
                    "invalid gateway token",
                    status_code=401,
                )
            return await call_next(request)

    @app.get("/health", response_model=None)
    async def _health() -> Any:
        # The standalone script advertises its own wire protocol.
        payload = {"status": "ok", "protocol_version": 1}
        if instance_nonce is not None:
            payload["instance_nonce"] = instance_nonce
        return payload

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
        runtime_headers = body.pop("runtime_headers", None)
        name = body.get("name", "")
        if not name:
            raise HTTPException(400, "name required")
        async with state.lock:
            by_name = state.clients.setdefault((agent_id, session_id), {})
            if name in by_name:
                raise HTTPException(
                    409,
                    f"{name!r} already exists for agent={agent_id!r} "
                    f"session={session_id!r}",
                )
            try:
                by_name[name] = await _build_client(body, runtime_headers)
                state.registrations[
                    agent_id,
                    session_id,
                    name,
                ] = secrets.token_hex(16)
            except HTTPException:
                raise
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            except Exception as e:  # noqa: BLE001
                raise HTTPException(500, f"connect failed: {e}") from e
        return {"ok": True}

    @app.delete("/mcps/{name}")
    async def _remove_mcp(
        name: str,
        agent_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        async with state.lock:
            client = _lookup(agent_id, session_id, name)
            del state.clients[(agent_id, session_id)][name]
            state.registrations.pop((agent_id, session_id, name), None)
            if not state.clients[(agent_id, session_id)]:
                del state.clients[(agent_id, session_id)]
            if client.is_stateful and client.is_connected:
                await client.close()
        return {"ok": True}

    @app.put("/mcps/{name}/runtime-headers", status_code=204)
    async def _set_runtime_headers(
        name: str,
        request: Request,
        agent_id: str = "",
        session_id: str = "",
    ) -> Response:
        """Replace live headers without rebuilding the MCP client."""
        body = await request.json()
        headers = body.get("headers") if isinstance(body, dict) else None
        if not isinstance(headers, dict):
            raise HTTPException(
                400,
                "headers must be a JSON object",
            )
        try:
            async with state.lock:
                client = _lookup(agent_id, session_id, name)
                await client.set_runtime_headers(headers)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return Response(status_code=204)

    @app.get("/mcps/{name}/tools")
    async def _list_tools(
        name: str,
        agent_id: str = "",
        session_id: str = "",
        with_bindings: bool = False,
    ) -> Any:
        client = _lookup(agent_id, session_id, name)
        scope = _scope(agent_id, session_id, name) if with_bindings else None
        raw = await client.list_raw_tools()
        tools = [t.model_dump(mode="json") for t in raw]
        if scope is None:
            return tools
        if scope != _scope(agent_id, session_id, name):
            raise HTTPException(409, "MCP target changed during lookup")
        return {
            "tools": tools,
            "bindings": {t.name: _binding(scope, t) for t in raw},
        }

    @app.post("/mcps/{name}/approved-tools/{tool}")
    async def _call_approved_tool(
        name: str,
        tool: str,
        request: Request,
        agent_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        body = await request.json()
        expected = body.get("binding")
        client = _lookup(agent_id, session_id, name)
        scope = _scope(agent_id, session_id, name)
        if not isinstance(expected, str) or not expected.startswith(
            f"{scope}.",
        ):
            raise HTTPException(409, "The approved MCP registration changed")
        # Refresh even when an upstream does not notify on catalog changes.
        raw = await client.list_raw_tools()
        if not any(t.name == tool for t in raw):
            raise HTTPException(409, "The approved MCP tool is unavailable")
        resolved = await client.get_tool(tool)
        if (
            scope != _scope(agent_id, session_id, name)
            # Compare the descriptor of the instance we are about to invoke.
            # pylint: disable-next=protected-access
            or expected != _binding(scope, resolved._tool)
        ):
            raise HTTPException(409, "The approved MCP target changed")
        resolved.stream_progress = False
        bound = resolved.bind_for_dispatch()
        chunk = await bound(**(body.get("arguments") or {}))
        return {"chunk": chunk.model_dump(mode="json")}

    @app.post("/mcps/{name}/tools/{tool}")
    async def _call_tool(
        name: str,
        tool: str,
        request: Request,
        agent_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        client = _lookup(agent_id, session_id, name)
        body = await request.json()
        arguments = body.get("arguments") or {}
        try:
            tool_obj = await client.get_tool(tool)
            # Sandbox calls return only the final result in this gateway.
            tool_obj.stream_progress = False
            chunk = await tool_obj(**arguments)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, str(e)) from e
        return {"chunk": chunk.model_dump(mode="json")}

    return app


async def _run(
    port: int,
    auth_token: str | None = None,
    instance_nonce: str | None = None,
) -> None:
    """Start uvicorn on an empty registry, clean up upstreams on exit."""
    state = _State()
    app = _build_app(
        state,
        auth_token=auth_token,
        instance_nonce=instance_nonce,
    )
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
    # Accepted and ignored — kept so an image shipping an older
    # launch command still starts.
    parser.add_argument("--config", default=None)
    parser.add_argument("--port", type=int, default=5600)
    parser.add_argument("--auth-token")
    parser.add_argument("--instance-nonce")
    args = parser.parse_args()
    asyncio.run(
        _run(
            args.port,
            auth_token=args.auth_token,
            instance_nonce=args.instance_nonce,
        ),
    )


if __name__ == "__main__":
    main()
