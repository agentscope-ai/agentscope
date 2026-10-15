# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for local and gateway MCP progress streaming."""
import asyncio
import json
import time
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from mcp.types import CallToolResult, ResourceLink, TextContent, Tool

from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import (
    MCPTool,
    ToolChunk,
    ToolResponse,
    ToolMiddlewareBase,
)
from agentscope.workspace._gateway_client import (
    GatewayMCPTool,
    GatewayMCPClient,
)
from agentscope.workspace._mcp_gateway._mcp_gateway_app import (
    _build_app,
    _State,
    _result_chunk,
)


def _chunk_view(chunk: ToolChunk) -> dict[str, Any]:
    """Return a deterministic full view without generated identifiers."""
    return {
        "content": [
            block.model_dump(
                mode="json",
                exclude={"id", "created_at", "finished_at"},
            )
            for block in chunk.content
        ],
        "state": chunk.state,
        "is_last": chunk.is_last,
        "metadata": chunk.metadata,
    }


class _ProgressClient:
    """MCP client stand-in that reports progress before its final result."""

    def __init__(self) -> None:
        self.cancelled = asyncio.Event()
        self.call: dict[str, Any] = {}

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        read_timeout_seconds: float | None,
        progress_callback: Any,
    ) -> CallToolResult:
        """Emit two progress notifications and one final text result."""
        self.call = {
            "name": name,
            "arguments": arguments,
            "read_timeout_seconds": read_timeout_seconds,
        }
        await progress_callback(1.0, 2.0, "first")
        await progress_callback(2.0, 2.0, "second")
        return CallToolResult(
            content=[TextContent(type="text", text="done")],
            is_error=False,
        )


class _BlockingProgressClient(_ProgressClient):
    """MCP client stand-in that blocks until its call is cancelled."""

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        read_timeout_seconds: float | None,
        progress_callback: Any,
    ) -> CallToolResult:
        """Emit one update, then expose cancellation to the test."""
        await progress_callback(1.0, None, "waiting")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class _FailingProgressClient(_ProgressClient):
    """MCP client stand-in that fails after reporting progress."""

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        read_timeout_seconds: float | None,
        progress_callback: Any,
    ) -> CallToolResult:
        """Emit one update before raising a tool execution error."""
        await progress_callback(1.0, 2.0, "first")
        raise RuntimeError("tool failed")


class _ResourceLinkClient(_ProgressClient):
    """MCP client stand-in returning a link without a MIME type."""

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        read_timeout_seconds: float | None,
        progress_callback: Any,
    ) -> CallToolResult:
        """Return one valid resource link without reporting progress."""
        return CallToolResult(
            content=[
                ResourceLink(
                    name="download",
                    uri="https://example.com/file",
                ),
            ],
            is_error=False,
        )


class _RegisteredClient:
    """Gateway registry stand-in exposing the lightweight raw call API."""

    def __init__(self, client: _ProgressClient) -> None:
        self.client = client

    async def _get_raw_tool(self, name: str) -> Tool:
        """Return the registered raw tool descriptor."""
        if name != "work":
            raise ValueError(f"Unknown tool {name!r}.")
        return Tool(
            name="work",
            description="Work with progress.",
            input_schema={"type": "object"},
        )

    async def _call_raw_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        progress_callback: Any | None = None,
    ) -> CallToolResult:
        """Call the underlying SDK stand-in without an AgentScope adapter."""
        await self._get_raw_tool(name)
        return await self.client.call_tool(
            name,
            arguments or {},
            None,
            progress_callback,
        )


class _GatewayFacade:
    """Host-side gateway stand-in with deterministic polling responses."""

    def __init__(self, progress: ToolChunk, final: ToolChunk) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.responses = [
            (202, b'{"call_id":"call-1"}'),
            (
                200,
                json.dumps(
                    {
                        "events": [
                            {
                                "sequence": 1,
                                "chunk": progress.model_dump(mode="json"),
                            },
                        ],
                        "done": False,
                        "cancelled": False,
                        "error": None,
                    },
                ).encode(),
            ),
            (
                200,
                json.dumps(
                    {
                        "events": [
                            {
                                "sequence": 2,
                                "chunk": final.model_dump(mode="json"),
                            },
                        ],
                        "done": True,
                        "cancelled": False,
                        "error": None,
                    },
                ).encode(),
            ),
            (200, b'{"ok":true}'),
        ]

    async def exec_request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> tuple[int, bytes]:
        """Return the next prepared gateway response."""
        self.requests.append((method, path, kwargs))
        return self.responses.pop(0)


def _make_tool(client: Any) -> MCPTool:
    """Build one MCP tool around a supplied client stand-in."""
    return MCPTool(
        mcp_name="remote",
        tool=Tool(
            name="work",
            description="Work with progress.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
        ),
        session=client,
        timeout=7.0,
    )


class MCPProgressTest(unittest.IsolatedAsyncioTestCase):
    """MCP progress must remain transient and ordered."""

    async def test_local_progress_is_not_model_content(self) -> None:
        """Progress chunks stream, but only the result becomes content."""
        client = _ProgressClient()
        tool = _make_tool(client)

        stream = await tool(value=3)
        chunks = [chunk async for chunk in stream]

        self.assertEqual(
            [_chunk_view(chunk) for chunk in chunks],
            [
                {
                    "content": [],
                    "state": ToolResultState.RUNNING,
                    "is_last": False,
                    "metadata": {
                        "mcp_progress": {
                            "progress": 1.0,
                            "total": 2.0,
                            "message": "first",
                        },
                    },
                },
                {
                    "content": [],
                    "state": ToolResultState.RUNNING,
                    "is_last": False,
                    "metadata": {
                        "mcp_progress": {
                            "progress": 2.0,
                            "total": 2.0,
                            "message": "second",
                        },
                    },
                },
                {
                    "content": [{"type": "text", "text": "done"}],
                    "state": ToolResultState.RUNNING,
                    "is_last": True,
                    "metadata": {},
                },
            ],
        )
        response = ToolResponse()
        for chunk in chunks:
            response.append_chunk(chunk)
        self.assertEqual(
            {
                "content": [
                    block.model_dump(
                        mode="json",
                        exclude={"id", "created_at", "finished_at"},
                    )
                    for block in response.content
                ],
                "state": response.state,
                "metadata": response.metadata,
                "call": client.call,
            },
            {
                "content": [{"type": "text", "text": "done"}],
                "state": ToolResultState.SUCCESS,
                "metadata": {
                    "mcp_progress": {
                        "progress": 2.0,
                        "total": 2.0,
                        "message": "second",
                    },
                },
                "call": {
                    "name": "work",
                    "arguments": {"value": 3},
                    "read_timeout_seconds": 7.0,
                },
            },
        )

    async def test_final_calls_preserve_middleware_interception(self) -> None:
        """Both MCP final-result APIs respect an intercepting middleware."""
        expected = ToolChunk(content=[TextBlock(text="blocked")])

        class _Block(ToolMiddlewareBase):
            """Return a result without calling the upstream tool."""

            async def on_tool_call(
                self,
                tool: Any,
                input_kwargs: Any,
                next_handler: Any,
            ) -> Any:
                yield expected

        sdk = _ProgressClient()
        local = _make_tool(sdk)
        gateway = _GatewayFacade(expected, expected)
        remote = GatewayMCPTool(
            mcp_name="remote",
            tool=Tool(name="work", input_schema={}),
            gateway=gateway,  # type: ignore[arg-type]
        )
        for tool in (local, remote):
            tool._middlewares = [_Block()]
            result = await tool.call_final(value=3)
            self.assertEqual(result.model_dump(), expected.model_dump())
        self.assertEqual(
            {"sdk": sdk.call, "gateway": gateway.requests},
            {"sdk": {}, "gateway": []},
        )

    async def test_gateway_rejects_internal_raw_calls(self) -> None:
        """Internal raw APIs never fall back to a proxy's host transport."""
        for stateful in (False, True):
            proxy = GatewayMCPClient(
                name="remote",
                is_stateful=stateful,
                mcp_config={"type": "http_mcp", "url": "http://unused/mcp"},
            )
            with patch.object(
                GatewayMCPClient,
                "_get_client_gen",
            ) as transport:
                with self.assertRaises(NotImplementedError):
                    await proxy._call_raw_tool("work", {})
                transport.assert_not_called()

    async def test_local_progress_buffer_retains_latest_snapshots(
        self,
    ) -> None:
        """A paused consumer retains bounded progress and the final result."""
        resume = asyncio.Event()
        flooded = asyncio.Event()

        class _Flood(_ProgressClient):
            """Emit many updates while the consumer is paused."""

            async def call_tool(
                self,
                name: str,
                arguments: dict[str, Any],
                read_timeout_seconds: float | None,
                progress_callback: Any,
            ) -> CallToolResult:
                await progress_callback(0, None, "start")
                await resume.wait()
                for value in range(1, 3001):
                    await progress_callback(value, 3000, None)
                flooded.set()
                return CallToolResult(
                    content=[TextContent(type="text", text="done")],
                )

        stream = await _make_tool(_Flood())()
        try:
            first = await anext(stream)
            resume.set()
            await asyncio.wait_for(flooded.wait(), 2)
            chunks = [chunk async for chunk in stream]
        finally:
            await stream.aclose()
        self.assertEqual(
            {
                "first": first.metadata["mcp_progress"]["progress"],
                "retained": [
                    chunk.metadata["mcp_progress"]["progress"]
                    for chunk in chunks[:-1]
                ],
                "final": _chunk_view(chunks[-1]),
            },
            {
                "first": 0,
                "retained": list(range(1977, 3001)),
                "final": {
                    "content": [{"type": "text", "text": "done"}],
                    "state": ToolResultState.RUNNING,
                    "is_last": True,
                    "metadata": {},
                },
            },
        )

    async def test_local_call_final_returns_only_the_result(self) -> None:
        """The compatibility entry discards transient progress chunks."""
        tool = _make_tool(_ProgressClient())

        result = await tool.call_final(value=3)

        self.assertEqual(
            _chunk_view(result),
            {
                "content": [{"type": "text", "text": "done"}],
                "state": ToolResultState.RUNNING,
                "is_last": True,
                "metadata": {},
            },
        )

    async def test_resource_link_without_mime_type_uses_fallback(self) -> None:
        """A valid MCP resource link may omit its MIME type."""
        tool = _make_tool(_ResourceLinkClient())

        result = await tool.call_final(value=3)

        self.assertEqual(
            [
                block.model_dump(
                    mode="json",
                    exclude={"id", "created_at", "finished_at"},
                )
                for block in result.content
            ],
            [
                {
                    "type": "data",
                    "source": {
                        "type": "url",
                        "url": "https://example.com/file",
                        "media_type": "application/octet-stream",
                    },
                    "name": None,
                },
            ],
        )

    async def test_invalid_resource_uri_preserves_other_content(self) -> None:
        """Local and gateway conversions isolate invalid resource URLs."""
        for uri in ("reports/q3.pdf", "http://[broken"):
            with self.subTest(uri=uri):
                result = CallToolResult(
                    content=[
                        TextContent(type="text", text="before"),
                        ResourceLink(name="report", uri=uri),
                        ResourceLink(
                            name="valid",
                            uri="https://example.com/file",
                        ),
                        TextContent(type="text", text="after"),
                    ],
                )
                client = _ProgressClient()
                with patch.object(
                    client,
                    "call_tool",
                    new=AsyncMock(return_value=result),
                ):
                    local = await _make_tool(client).call_final()
                gateway = ToolChunk.model_validate(_result_chunk(result))
                expected = {
                    "content": [
                        {"type": "text", "text": "before"},
                        {
                            "type": "text",
                            "text": f"<unconvertible MCP resource URI: {uri}>",
                        },
                        {
                            "type": "data",
                            "source": {
                                "type": "url",
                                "url": "https://example.com/file",
                                "media_type": "application/octet-stream",
                            },
                            "name": None,
                        },
                        {"type": "text", "text": "after"},
                    ],
                    "state": ToolResultState.RUNNING,
                    "is_last": True,
                    "metadata": {},
                }
                self.assertEqual(
                    {
                        "local": _chunk_view(local),
                        "gateway": _chunk_view(gateway),
                    },
                    {"local": expected, "gateway": expected},
                )

    async def test_gateway_proxy_streams_and_acknowledges(self) -> None:
        """The host proxy preserves gateway event order and cleans up."""
        progress = ToolChunk(
            content=[],
            is_last=False,
            metadata={
                "mcp_progress": {
                    "progress": 1.0,
                    "total": 1.0,
                    "message": "working",
                },
            },
            id="progress",
        )
        final = ToolChunk(
            content=[TextBlock(text="done", id="text")],
            is_last=True,
            id="final",
        )
        gateway = _GatewayFacade(progress, final)
        tool = GatewayMCPTool(
            mcp_name="remote",
            tool=Tool(
                name="work",
                description="Work with progress.",
                input_schema={"type": "object"},
            ),
            gateway=gateway,  # type: ignore[arg-type]
            agent_id="agent",
            session_id="session",
        )

        stream = await tool(value=3)
        chunks = [chunk async for chunk in stream]

        self.assertEqual(
            {
                "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
                "requests": gateway.requests,
                "remaining_responses": gateway.responses,
            },
            {
                "chunks": [
                    progress.model_dump(mode="json"),
                    final.model_dump(mode="json"),
                ],
                "requests": [
                    (
                        "POST",
                        "/mcps/remote/tools/work/calls",
                        {
                            "params": {
                                "agent_id": "agent",
                                "session_id": "session",
                            },
                            "body": {"arguments": {"value": 3}},
                        },
                    ),
                    (
                        "GET",
                        "/tool-calls/call-1/events",
                        {
                            "params": {
                                "agent_id": "agent",
                                "session_id": "session",
                                "after": "0",
                                "wait": "1.0",
                            },
                        },
                    ),
                    (
                        "GET",
                        "/tool-calls/call-1/events",
                        {
                            "params": {
                                "agent_id": "agent",
                                "session_id": "session",
                                "after": "1",
                                "wait": "1.0",
                            },
                        },
                    ),
                    (
                        "DELETE",
                        "/tool-calls/call-1",
                        {
                            "params": {
                                "agent_id": "agent",
                                "session_id": "session",
                            },
                        },
                    ),
                ],
                "remaining_responses": [],
            },
        )

    async def test_gateway_call_final_returns_only_the_result(self) -> None:
        """Gateway MCP tools expose the same final-result entry."""
        progress = ToolChunk(
            content=[],
            is_last=False,
            id="progress",
        )
        final = ToolChunk(
            content=[TextBlock(text="done", id="text")],
            is_last=True,
            id="final",
        )
        tool = GatewayMCPTool(
            mcp_name="remote",
            tool=Tool(
                name="work",
                description="Work with progress.",
                input_schema={"type": "object"},
            ),
            gateway=_GatewayFacade(progress, final),  # type: ignore[arg-type]
            agent_id="agent",
            session_id="session",
        )

        result = await tool.call_final(value=3)

        self.assertEqual(
            result.model_dump(mode="json"),
            final.model_dump(mode="json"),
        )

    async def test_closing_local_stream_cancels_mcp_call(self) -> None:
        """Closing a progress stream cancels its in-flight MCP request."""
        client = _BlockingProgressClient()
        tool = _make_tool(client)

        stream = await tool(value=3)
        first = await anext(stream)
        await stream.aclose()

        self.assertEqual(
            {
                "first": _chunk_view(first),
                "cancelled": client.cancelled.is_set(),
            },
            {
                "first": {
                    "content": [],
                    "state": ToolResultState.RUNNING,
                    "is_last": False,
                    "metadata": {
                        "mcp_progress": {
                            "progress": 1.0,
                            "total": None,
                            "message": "waiting",
                        },
                    },
                },
                "cancelled": True,
            },
        )


class GatewayProgressRouteTest(unittest.TestCase):
    """The in-workspace gateway stores ordered progress and final chunks."""

    def test_background_call_lifecycle(self) -> None:
        """Start, poll, and acknowledge one complete gateway call."""
        state = _State()
        state.clients[("agent", "session")] = {
            "remote": _RegisteredClient(  # type: ignore[dict-item]
                _ProgressClient(),
            ),
        }

        with TestClient(_build_app(state)) as client:
            start = client.post(
                "/mcps/remote/tools/work/calls",
                params={"agent_id": "agent", "session_id": "session"},
                json={"arguments": {"value": 3}},
            )
            call_id = start.json()["call_id"]
            poll = client.get(
                f"/tool-calls/{call_id}/events",
                params={
                    "agent_id": "agent",
                    "session_id": "session",
                    "after": 0,
                    "wait": 1.0,
                },
            )
            cleanup = client.delete(
                f"/tool-calls/{call_id}",
                params={"agent_id": "agent", "session_id": "session"},
            )

        payload = poll.json()
        self.assertEqual(
            {
                "start_status": start.status_code,
                "poll_status": poll.status_code,
                "event_views": [
                    _chunk_view(ToolChunk.model_validate(event["chunk"]))
                    for event in payload["events"]
                ],
                "done": payload["done"],
                "cancelled": payload["cancelled"],
                "error": payload["error"],
                "cleanup_status": cleanup.status_code,
                "remaining_calls": state.tool_calls,
            },
            {
                "start_status": 202,
                "poll_status": 200,
                "event_views": [
                    {
                        "content": [],
                        "state": ToolResultState.RUNNING,
                        "is_last": False,
                        "metadata": {
                            "mcp_progress": {
                                "progress": 1.0,
                                "total": 2.0,
                                "message": "first",
                            },
                        },
                    },
                    {
                        "content": [],
                        "state": ToolResultState.RUNNING,
                        "is_last": False,
                        "metadata": {
                            "mcp_progress": {
                                "progress": 2.0,
                                "total": 2.0,
                                "message": "second",
                            },
                        },
                    },
                    {
                        "content": [{"type": "text", "text": "done"}],
                        "state": ToolResultState.RUNNING,
                        "is_last": True,
                        "metadata": {},
                    },
                ],
                "done": True,
                "cancelled": False,
                "error": None,
                "cleanup_status": 200,
                "remaining_calls": {},
            },
        )

    def test_poll_acknowledges_consumed_events(self) -> None:
        """The next sequence cursor removes confirmed gateway events."""
        state = _State()
        state.clients[("agent", "session")] = {
            "remote": _RegisteredClient(  # type: ignore[dict-item]
                _ProgressClient(),
            ),
        }

        with TestClient(_build_app(state)) as client:
            start = client.post(
                "/mcps/remote/tools/work/calls",
                params={"agent_id": "agent", "session_id": "session"},
                json={"arguments": {"value": 3}},
            )
            call_id = start.json()["call_id"]
            first = client.get(
                f"/tool-calls/{call_id}/events",
                params={
                    "agent_id": "agent",
                    "session_id": "session",
                    "after": 0,
                    "wait": 1.0,
                },
            )
            second = client.get(
                f"/tool-calls/{call_id}/events",
                params={
                    "agent_id": "agent",
                    "session_id": "session",
                    "after": 2,
                    "wait": 0.0,
                },
            )
            stored_sequences = [
                event["sequence"]
                for event in state.tool_calls[
                    ("agent", "session", call_id)
                ].events
            ]
            client.delete(
                f"/tool-calls/{call_id}",
                params={"agent_id": "agent", "session_id": "session"},
            )

        self.assertEqual(
            {
                "first": [
                    event["sequence"] for event in first.json()["events"]
                ],
                "second": [
                    event["sequence"] for event in second.json()["events"]
                ],
                "stored": stored_sequences,
            },
            {
                "first": [1, 2, 3],
                "second": [3],
                "stored": [3],
            },
        )

    def test_unacknowledged_event_buffer_is_bounded(self) -> None:
        """A slow poller cannot grow one call's event buffer forever."""
        state = _State()
        state.clients[("agent", "session")] = {
            "remote": _RegisteredClient(  # type: ignore[dict-item]
                _ProgressClient(),
            ),
        }

        with patch(
            "agentscope.workspace._mcp_gateway._mcp_gateway_app."
            "_MAX_UNACKNOWLEDGED_EVENTS",
            2,
        ), TestClient(_build_app(state)) as client:
            start = client.post(
                "/mcps/remote/tools/work/calls",
                params={"agent_id": "agent", "session_id": "session"},
                json={"arguments": {"value": 3}},
            )
            call_id = start.json()["call_id"]
            poll = client.get(
                f"/tool-calls/{call_id}/events",
                params={
                    "agent_id": "agent",
                    "session_id": "session",
                    "after": 0,
                    "wait": 1.0,
                },
            )
            client.delete(
                f"/tool-calls/{call_id}",
                params={"agent_id": "agent", "session_id": "session"},
            )

        self.assertEqual(
            {
                "sequences": [
                    event["sequence"] for event in poll.json()["events"]
                ],
                "is_last": [
                    event["chunk"]["is_last"]
                    for event in poll.json()["events"]
                ],
            },
            {
                "sequences": [2, 3],
                "is_last": [False, True],
            },
        )

    def test_gateway_resource_link_without_mime_uses_fallback(self) -> None:
        """The lightweight gateway converter preserves a MIME-less link."""
        state = _State()
        state.clients[("agent", "session")] = {
            "remote": _RegisteredClient(  # type: ignore[dict-item]
                _ResourceLinkClient(),
            ),
        }

        with TestClient(_build_app(state)) as client:
            start = client.post(
                "/mcps/remote/tools/work/calls",
                params={"agent_id": "agent", "session_id": "session"},
                json={"arguments": {"value": 3}},
            )
            call_id = start.json()["call_id"]
            poll = client.get(
                f"/tool-calls/{call_id}/events",
                params={
                    "agent_id": "agent",
                    "session_id": "session",
                    "after": 0,
                    "wait": 1.0,
                },
            )
            client.delete(
                f"/tool-calls/{call_id}",
                params={"agent_id": "agent", "session_id": "session"},
            )

        chunks = [
            ToolChunk.model_validate(event["chunk"])
            for event in poll.json()["events"]
        ]
        self.assertEqual(
            {
                "start_status": start.status_code,
                "poll_status": poll.status_code,
                "chunks": [_chunk_view(chunk) for chunk in chunks],
            },
            {
                "start_status": 202,
                "poll_status": 200,
                "chunks": [
                    {
                        "content": [
                            {
                                "type": "data",
                                "source": {
                                    "type": "url",
                                    "url": "https://example.com/file",
                                    "media_type": "application/octet-stream",
                                },
                                "name": None,
                            },
                        ],
                        "state": ToolResultState.RUNNING,
                        "is_last": True,
                        "metadata": {},
                    },
                ],
            },
        )

    def test_delete_cancels_background_call(self) -> None:
        """Deleting an unfinished gateway call cancels its MCP request."""
        state = _State()
        mcp_client = _BlockingProgressClient()
        state.clients[("agent", "session")] = {
            "remote": _RegisteredClient(  # type: ignore[dict-item]
                mcp_client,
            ),
        }

        with TestClient(_build_app(state)) as client:
            start = client.post(
                "/mcps/remote/tools/work/calls",
                params={"agent_id": "agent", "session_id": "session"},
                json={"arguments": {"value": 3}},
            )
            call_id = start.json()["call_id"]
            poll = client.get(
                f"/tool-calls/{call_id}/events",
                params={
                    "agent_id": "agent",
                    "session_id": "session",
                    "after": 0,
                    "wait": 1.0,
                },
            )
            cleanup = client.delete(
                f"/tool-calls/{call_id}",
                params={"agent_id": "agent", "session_id": "session"},
            )

        self.assertEqual(
            {
                "start_status": start.status_code,
                "poll": {
                    "events": [
                        _chunk_view(ToolChunk.model_validate(event["chunk"]))
                        for event in poll.json()["events"]
                    ],
                    "done": poll.json()["done"],
                },
                "cleanup_status": cleanup.status_code,
                "cancelled": mcp_client.cancelled.is_set(),
                "remaining_calls": state.tool_calls,
            },
            {
                "start_status": 202,
                "poll": {
                    "events": [
                        {
                            "content": [],
                            "state": ToolResultState.RUNNING,
                            "is_last": False,
                            "metadata": {
                                "mcp_progress": {
                                    "progress": 1.0,
                                    "total": None,
                                    "message": "waiting",
                                },
                            },
                        },
                    ],
                    "done": False,
                },
                "cleanup_status": 200,
                "cancelled": True,
                "remaining_calls": {},
            },
        )

    def test_idle_active_call_is_reaped_without_another_request(self) -> None:
        """The periodic reaper cancels a call whose host stops polling."""
        state = _State()
        mcp_client = _BlockingProgressClient()
        state.clients[("agent", "session")] = {
            "remote": _RegisteredClient(  # type: ignore[dict-item]
                mcp_client,
            ),
        }

        with patch(
            "agentscope.workspace._mcp_gateway._mcp_gateway_app."
            "_ACTIVE_CALL_IDLE_TTL_SECONDS",
            0.2,
        ), patch(
            "agentscope.workspace._mcp_gateway._mcp_gateway_app."
            "_CALL_REAPER_INTERVAL_SECONDS",
            0.01,
        ), TestClient(
            _build_app(state),
        ) as client:
            start = client.post(
                "/mcps/remote/tools/work/calls",
                params={"agent_id": "agent", "session_id": "session"},
                json={"arguments": {"value": 3}},
            )
            call_id = start.json()["call_id"]
            first_poll = client.get(
                f"/tool-calls/{call_id}/events",
                params={
                    "agent_id": "agent",
                    "session_id": "session",
                    "after": 0,
                    "wait": 1.0,
                },
            )
            deadline = time.monotonic() + 2.0
            while state.tool_calls and time.monotonic() < deadline:
                time.sleep(0.01)
            retained_calls = dict(state.tool_calls)

        self.assertEqual(
            {
                "start_status": start.status_code,
                "first_poll_status": first_poll.status_code,
                "cancelled": mcp_client.cancelled.is_set(),
                "retained_calls": retained_calls,
            },
            {
                "start_status": 202,
                "first_poll_status": 200,
                "cancelled": True,
                "retained_calls": {},
            },
        )

    def test_background_call_reports_error_after_progress(self) -> None:
        """A failed call preserves progress and exposes its terminal error."""
        state = _State()
        state.clients[("agent", "session")] = {
            "remote": _RegisteredClient(  # type: ignore[dict-item]
                _FailingProgressClient(),
            ),
        }

        with TestClient(_build_app(state)) as client:
            start = client.post(
                "/mcps/remote/tools/work/calls",
                params={"agent_id": "agent", "session_id": "session"},
                json={"arguments": {"value": 3}},
            )
            call_id = start.json()["call_id"]
            poll = client.get(
                f"/tool-calls/{call_id}/events",
                params={
                    "agent_id": "agent",
                    "session_id": "session",
                    "after": 0,
                    "wait": 1.0,
                },
            )
            cleanup = client.delete(
                f"/tool-calls/{call_id}",
                params={"agent_id": "agent", "session_id": "session"},
            )

        payload = poll.json()
        self.assertEqual(
            {
                "start_status": start.status_code,
                "poll_status": poll.status_code,
                "events": [
                    _chunk_view(ToolChunk.model_validate(event["chunk"]))
                    for event in payload["events"]
                ],
                "done": payload["done"],
                "cancelled": payload["cancelled"],
                "error": payload["error"],
                "cleanup_status": cleanup.status_code,
                "remaining_calls": state.tool_calls,
            },
            {
                "start_status": 202,
                "poll_status": 200,
                "events": [
                    {
                        "content": [],
                        "state": ToolResultState.RUNNING,
                        "is_last": False,
                        "metadata": {
                            "mcp_progress": {
                                "progress": 1.0,
                                "total": 2.0,
                                "message": "first",
                            },
                        },
                    },
                ],
                "done": True,
                "cancelled": False,
                "error": "tool failed",
                "cleanup_status": 200,
                "remaining_calls": {},
            },
        )


if __name__ == "__main__":
    unittest.main()
