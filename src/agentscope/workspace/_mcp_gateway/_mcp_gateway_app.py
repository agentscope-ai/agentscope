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
    POST   /mcps/{name}/tools/{tool}/calls
    GET    /tool-calls/{call_id}/events
    DELETE /tool-calls/{call_id}

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
import secrets
import uuid
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

import mcp.types
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

# pylint: disable=protected-access
from agentscope.mcp import MCPClient
from agentscope.mcp._content import _convert_mcp_content


_TERMINAL_CALL_TTL_SECONDS = 300.0
_ACTIVE_CALL_IDLE_TTL_SECONDS = 300.0
_CALL_REAPER_INTERVAL_SECONDS = 30.0
_MAX_UNACKNOWLEDGED_EVENTS = 1024


def _progress_chunk(
    progress: float,
    total: float | None,
    message: str | None,
) -> dict[str, Any]:
    """Build the wire representation of one MCP progress update."""
    return {
        "content": [],
        "state": "running",
        "is_last": False,
        "metadata": {
            "mcp_progress": {
                "progress": progress,
                "total": total,
                "message": message,
            },
        },
    }


def _result_chunk(result: mcp.types.CallToolResult) -> dict[str, Any]:
    """Convert a raw MCP result to the gateway's ToolChunk wire shape."""
    return {
        "content": _convert_mcp_content(result.content),
        "state": "error" if result.is_error else "running",
        "is_last": True,
        "metadata": {},
    }


class _ToolCall:
    """One background call with a single consumer's ordered output events.

    Acknowledgements discard shared events, so independent consumers must
    not poll the same call ID with separate cursors.
    """

    def __init__(self, mcp_name: str) -> None:
        self.mcp_name = mcp_name
        self.events: deque[dict[str, Any]] = deque()
        self.next_sequence = 1
        self.done = False
        self.cancelled = False
        self.error: str | None = None
        self.task: asyncio.Task[None] | None = None
        self.changed = asyncio.Event()
        self.updated_at = asyncio.get_running_loop().time()
        self.last_polled_at = self.updated_at

    def append(self, chunk: dict[str, Any]) -> None:
        """Append one chunk and wake long-polling consumers."""
        if len(self.events) >= _MAX_UNACKNOWLEDGED_EVENTS:
            self.events.popleft()
        self.events.append(
            {
                "sequence": self.next_sequence,
                "chunk": chunk,
            },
        )
        self.next_sequence += 1
        self.updated_at = asyncio.get_running_loop().time()
        self.changed.set()

    def touch(self) -> None:
        """Renew the host polling lease for an active call."""
        self.last_polled_at = asyncio.get_running_loop().time()

    def acknowledge(self, sequence: int) -> None:
        """Discard events confirmed by the polling consumer."""
        while self.events and self.events[0]["sequence"] <= sequence:
            self.events.popleft()

    def finish(
        self,
        *,
        error: str | None = None,
        cancelled: bool = False,
    ) -> None:
        """Mark the call terminal and wake long-polling consumers."""
        self.done = True
        self.error = error
        self.cancelled = cancelled
        self.updated_at = asyncio.get_running_loop().time()
        self.changed.set()


class _State:
    """Mutable runtime state shared by FastAPI routes."""

    def __init__(self) -> None:
        self.clients: dict[tuple[str, str], dict[str, MCPClient]] = {}
        self.tool_calls: dict[tuple[str, str, str], _ToolCall] = {}
        self.lock = asyncio.Lock()

    async def prune_tool_calls(self) -> None:
        """Remove expired terminal calls and abandoned active calls."""
        now = asyncio.get_running_loop().time()
        stale = [
            key
            for key, call in self.tool_calls.items()
            if (
                call.done
                and now - call.updated_at >= _TERMINAL_CALL_TTL_SECONDS
            )
            or (
                not call.done
                and now - call.last_polled_at >= _ACTIVE_CALL_IDLE_TTL_SECONDS
            )
        ]
        tasks: list[asyncio.Task[None]] = []
        for key in stale:
            call = self.tool_calls.pop(key)
            if call.task is not None and not call.task.done():
                call.task.cancel()
                tasks.append(call.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close_tool_calls(self) -> None:
        """Cancel and remove every retained background tool call."""
        calls = list(self.tool_calls.values())
        self.tool_calls.clear()
        tasks = [
            call.task
            for call in calls
            if call.task is not None and not call.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def _build_client(
    spec: dict[str, Any],
    runtime_headers: dict[str, str] | None = None,
) -> MCPClient:
    """Validate a spec into an ``MCPClient``, connect if stateful,
    and prime its tool cache.

    ``runtime_headers`` are applied before connecting: a rotated
    credential has to be in place for the handshake below, and it is
    not part of ``spec`` because it must never be persisted.
    """
    client = MCPClient.model_validate(spec)
    if runtime_headers:
        await client.set_runtime_headers(runtime_headers)
    if client.is_stateful:
        await client.connect()
    await client.list_raw_tools()
    return client


# pylint: disable=too-many-statements
def _build_app(
    state: _State,
    auth_token: str | None = None,
    instance_nonce: str | None = None,
) -> FastAPI:
    """Build the FastAPI app with all routes wired against ``state``."""

    async def _reap_tool_calls() -> None:
        """Periodically enforce terminal TTLs and active polling leases."""
        while True:
            await asyncio.sleep(_CALL_REAPER_INTERVAL_SECONDS)
            await state.prune_tool_calls()

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> Any:
        """Own the background call reaper for the application lifetime."""
        reaper_task = asyncio.create_task(_reap_tool_calls())
        try:
            yield
        finally:
            reaper_task.cancel()
            try:
                await reaper_task
            except asyncio.CancelledError:
                pass
            await state.close_tool_calls()

    app = FastAPI(
        title="agentscope-workspace-mcp-gateway",
        lifespan=_lifespan,
    )

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

    async def _lookup_tool_call(
        agent_id: str,
        session_id: str,
        call_id: str,
    ) -> _ToolCall:
        """Resolve one background tool call or raise 404."""
        await state.prune_tool_calls()
        call = state.tool_calls.get((agent_id, session_id, call_id))
        if call is None:
            raise HTTPException(404, f"tool call {call_id!r} not found")
        call.touch()
        return call

    async def _run_tool_call(
        call: _ToolCall,
        client: MCPClient,
        tool: str,
        arguments: dict[str, Any],
    ) -> None:
        """Relay raw MCP progress and result data into a call record."""

        async def _on_progress(
            progress: float,
            total: float | None,
            message: str | None,
        ) -> None:
            call.append(_progress_chunk(progress, total, message))

        try:
            result = await client._call_raw_tool(
                tool,
                arguments,
                progress_callback=_on_progress,
            )
            call.append(_result_chunk(result))
        except asyncio.CancelledError:
            call.finish(cancelled=True)
            raise
        except Exception as e:  # noqa: BLE001
            call.finish(error=str(e))
        else:
            call.finish()

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
        if instance_nonce is not None:
            return {"status": "ok", "instance_nonce": instance_nonce}
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
            active_calls = [
                call
                for (call_agent, call_session, _), call in (
                    state.tool_calls.items()
                )
                if call_agent == agent_id
                and call_session == session_id
                and call.mcp_name == name
                and not call.done
            ]
            for call in active_calls:
                if call.task is not None:
                    call.task.cancel()
            await asyncio.gather(
                *(call.task for call in active_calls if call.task is not None),
                return_exceptions=True,
            )
            del state.clients[(agent_id, session_id)][name]
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
    ) -> list[dict[str, Any]]:
        client = _lookup(agent_id, session_id, name)
        raw = await client.list_raw_tools()
        return [t.model_dump(mode="json", by_alias=True) for t in raw]

    @app.post("/mcps/{name}/tools/{tool}/calls", status_code=202)
    async def _start_tool_call(
        name: str,
        tool: str,
        request: Request,
        agent_id: str = "",
        session_id: str = "",
    ) -> dict[str, str]:
        client = _lookup(agent_id, session_id, name)
        body = await request.json()
        arguments = body.get("arguments") or {}
        try:
            await client._get_raw_tool(tool)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e

        await state.prune_tool_calls()
        call_id = uuid.uuid4().hex
        call = _ToolCall(name)
        state.tool_calls[(agent_id, session_id, call_id)] = call
        call.task = asyncio.create_task(
            _run_tool_call(call, client, tool, arguments),
        )
        return {"call_id": call_id}

    @app.get("/tool-calls/{call_id}/events")
    async def _get_tool_call_events(
        call_id: str,
        after: int = 0,
        wait: float = 1.0,
        agent_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        call = await _lookup_tool_call(agent_id, session_id, call_id)
        call.acknowledge(after)

        def _pending_events() -> list[dict[str, Any]]:
            return list(call.events)

        events = _pending_events()
        if not events and not call.done and wait > 0:
            call.changed.clear()
            events = _pending_events()
            if not events and not call.done:
                try:
                    await asyncio.wait_for(
                        call.changed.wait(),
                        timeout=min(wait, 5.0),
                    )
                except TimeoutError:
                    pass
                events = _pending_events()

        return {
            "events": events,
            "done": call.done,
            "cancelled": call.cancelled,
            "error": call.error,
        }

    @app.delete("/tool-calls/{call_id}")
    async def _delete_tool_call(
        call_id: str,
        agent_id: str = "",
        session_id: str = "",
    ) -> dict[str, bool]:
        key = (agent_id, session_id, call_id)
        call = state.tool_calls.pop(key, None)
        if call is None:
            raise HTTPException(404, f"tool call {call_id!r} not found")
        if call.task is not None and not call.task.done():
            call.task.cancel()
            try:
                await call.task
            except asyncio.CancelledError:
                pass
        return {"ok": True}

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
            result = await client._call_raw_tool(tool, arguments)
            chunk = _result_chunk(result)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, str(e)) from e
        return {"chunk": chunk}

    return app


# pylint: enable=too-many-statements


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
        await state.close_tool_calls()
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
