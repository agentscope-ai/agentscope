# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for local MCP progress and sandbox final-result compatibility."""
import asyncio
import json
import unittest
import warnings
from itertools import product
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import anyio
from fastapi.testclient import TestClient
from mcp.types import (
    CallToolResult,
    ImageContent,
    ResourceLink,
    TextContent,
    Tool,
)
from utils import AnyString, MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.event import (
    CustomEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.mcp import MCPClient
from agentscope.model import ChatResponse
from agentscope.state import AgentState
from agentscope.tool import (
    MCPTool,
    ToolChunk,
    ToolResponse,
    ToolMiddlewareBase,
    Toolkit,
)
from agentscope.workspace._gateway_client import (
    GatewayClient,
    GatewayMCPTool,
)
from agentscope.workspace._mcp_gateway._mcp_gateway_app import (
    _build_app,
    _State,
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


class _CleanupProgressClient(_ProgressClient):
    """Model the SDK's shielded asynchronous cancellation notification."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cleanup_completed = False

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        read_timeout_seconds: float | None,
        progress_callback: Any,
    ) -> CallToolResult:
        """Block until cancelled, then simulate a delayed cancel write."""
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            with anyio.CancelScope(shield=True):
                await anyio.sleep(0.02)
                self.cleanup_completed = True
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
    """Gateway registry stand-in returning an AgentScope MCP tool."""

    def __init__(self, client: _ProgressClient) -> None:
        self.client = client

    async def get_tool(self, name: str) -> MCPTool:
        """Wrap the SDK stand-in using the real MCP adapter."""
        if name != "work":
            raise ValueError(f"Unknown tool {name!r}.")
        return MCPTool(
            mcp_name="remote",
            tool=Tool(name=name, input_schema={"type": "object"}),
            session=self.client,
        )


class _GatewayFacade:
    """Host-side gateway stand-in returning one final result."""

    def __init__(self, final: ToolChunk) -> None:
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.responses = [
            (
                200,
                json.dumps({"chunk": final.model_dump(mode="json")}).encode(),
            ),
        ]

    async def exec_request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> tuple[int, bytes]:
        """Record a single gateway request and return its result."""
        self.requests.append((method, path, kwargs))
        return self.responses.pop(0)


def _make_tool(client: Any, *, stream_progress: bool = True) -> MCPTool:
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
        stream_progress=stream_progress,
    )


class MCPProgressTest(unittest.IsolatedAsyncioTestCase):
    """MCP progress must remain transient and ordered."""

    async def test_client_gen_compatibility(self) -> None:
        """Accept connected clients and legacy two/three-item transports."""
        for streaming, transport_size in product((False, True), (0, 2, 3)):
            with self.subTest(streaming=streaming, size=transport_size):
                sdk = _ProgressClient()
                session = AsyncMock()
                session.__aenter__.return_value = session
                session.call_tool.side_effect = sdk.call_tool
                streams = tuple(object() for _ in range(transport_size))
                transport = AsyncMock()
                transport.__aenter__.return_value = streams or session
                factory = Mock(return_value=transport)
                tool = MCPTool(
                    mcp_name="remote",
                    tool=Tool(name="work", input_schema={}),
                    client_gen=factory,
                    stream_progress=streaming,
                )
                with (
                    patch(
                        "agentscope.tool._adapters.mcp.ClientSession",
                        return_value=session,
                    ) as session_class,
                    warnings.catch_warnings(record=True) as caught,
                ):
                    warnings.simplefilter("always", DeprecationWarning)
                    result = await tool(value=3)
                    chunks = (
                        [chunk async for chunk in result]
                        if streaming
                        else [result]
                    )

                self.assertEqual(
                    [_chunk_view(chunk) for chunk in chunks],
                    (
                        [
                            {
                                "content": [],
                                "state": ToolResultState.RUNNING,
                                "is_last": False,
                                "metadata": {
                                    "mcp_progress": {
                                        "progress": progress,
                                        "total": 2.0,
                                        "message": message,
                                    },
                                },
                            }
                            for progress, message in (
                                (1.0, "first"),
                                (2.0, "second"),
                            )
                        ]
                        if streaming
                        else []
                    )
                    + [
                        {
                            "content": [{"type": "text", "text": "done"}],
                            "state": ToolResultState.RUNNING,
                            "is_last": True,
                            "metadata": {},
                        },
                    ],
                )
                self.assertEqual(
                    [warning.category for warning in caught],
                    [DeprecationWarning] if transport_size else [],
                )
                if transport_size:
                    self.assertIn(
                        "connected mcp.Client",
                        str(caught[0].message),
                    )
                    session_class.assert_called_once_with(*streams[:2])
                    session.initialize.assert_awaited_once_with()
                    session.__aexit__.assert_awaited_once()
                else:
                    session_class.assert_not_called()
                    session.initialize.assert_not_awaited()
                factory.assert_called_once_with()
                transport.__aenter__.assert_awaited_once()
                transport.__aexit__.assert_awaited_once()

    async def test_default_calls_return_final_chunks(self) -> None:
        """Old direct calls keep returning a result without opting in."""
        for entry in ("__call__", "call"):
            with self.subTest(entry=entry):
                sdk = _ProgressClient()
                tool = MCPTool(
                    mcp_name="remote",
                    tool=Tool(name="work", input_schema={}),
                    session=sdk,
                )
                result = await getattr(tool, entry)(value=3)
                self.assertIsInstance(result, ToolChunk)
                self.assertEqual(
                    _chunk_view(result),
                    {
                        "content": [{"type": "text", "text": "done"}],
                        "state": ToolResultState.RUNNING,
                        "is_last": True,
                        "metadata": {},
                    },
                )
                self.assertFalse(tool.stream_progress)
                self.assertEqual(
                    sdk.call,
                    {
                        "name": "work",
                        "arguments": {"value": 3},
                        "read_timeout_seconds": None,
                    },
                )

    async def test_client_progress_configuration_reaches_tools(self) -> None:
        """Progress settings survive serialization and tool wrapping."""
        for setting in (None, False, True):
            for stateful in (False, True):
                with self.subTest(setting=setting, stateful=stateful):
                    spec = {
                        "name": "remote",
                        "is_stateful": stateful,
                        "mcp_config": {
                            "type": "http_mcp",
                            "url": "https://example.com/mcp",
                        },
                    }
                    if setting is not None:
                        spec["stream_progress"] = setting
                    client = MCPClient.model_validate(spec)
                    serialized = client.model_dump(mode="json")
                    restored = MCPClient.model_validate(serialized)
                    flags = []
                    for current in (client, restored):
                        current._cached_tools = [
                            Tool(name="work", input_schema={}),
                        ]
                        current._is_connected = True
                        current._client = _ProgressClient()
                        tool = await current.get_tool("work")
                        flags.append(tool.stream_progress)
                    expected = setting is True
                    self.assertEqual(
                        {
                            "serialized": serialized["stream_progress"],
                            "tools": flags,
                        },
                        {
                            "serialized": expected,
                            "tools": [expected, expected],
                        },
                    )

    async def test_agent_streams_progress_without_persisting_it(self) -> None:
        """Only local tools expose progress to sequential/concurrent agents."""
        final = ToolChunk(
            content=[TextBlock(text="done")],
            metadata={"source": "tool", "trace_id": "trace"},
        )

        class _Metadata(ToolMiddlewareBase):
            """Add ordinary metadata without changing progress delivery."""

            async def on_tool_call(  # pylint: disable=unused-argument
                self,
                tool: Any,
                input_kwargs: dict,
                next_handler: Any,
            ) -> Any:
                """Attach metadata while forwarding the tool's chunks."""
                async for chunk in next_handler(**input_kwargs):
                    chunk.metadata["trace_id"] = "trace"
                    if chunk.is_last:
                        chunk.metadata["source"] = "tool"
                    yield chunk

        for path in ("local", "gateway"):
            for concurrent, stream_progress in (
                (False, False),
                (False, True),
                (True, False),
                (True, True),
            ):
                with self.subTest(
                    path=path,
                    concurrent=concurrent,
                    stream_progress=stream_progress,
                ):
                    if path == "local":
                        tool = _make_tool(_ProgressClient())
                        tool._middlewares = [_Metadata()]
                        expected_progress = [
                            {
                                "progress": 1.0,
                                "total": 2.0,
                                "message": "first",
                            },
                            {
                                "progress": 2.0,
                                "total": 2.0,
                                "message": "second",
                            },
                        ]
                    else:
                        tool = GatewayMCPTool(
                            mcp_name="remote",
                            tool=Tool(
                                name="work",
                                input_schema={
                                    "type": "object",
                                },
                            ),
                            gateway=_GatewayFacade(final),
                            agent_id="agent",
                            session_id="session",
                        )
                        expected_progress = []
                    if path == "local":
                        tool.stream_progress = stream_progress
                    if not stream_progress:
                        expected_progress = []
                    tool.is_read_only = True
                    tool.is_concurrency_safe = concurrent
                    tool_call = ToolCallBlock(
                        id="call-1",
                        name=tool.name,
                        input='{"value": 3}',
                    )
                    model = MockModel()
                    model.set_responses(
                        [
                            ChatResponse(content=[tool_call], is_last=True),
                            ChatResponse(
                                content=[TextBlock(text="finished")],
                                is_last=True,
                            ),
                        ],
                    )
                    agent = Agent(
                        name="progress",
                        system_prompt="Test progress.",
                        model=model,
                        toolkit=Toolkit(tools=[tool]),
                        injection_config=InjectionConfig(
                            inject_runtime_state=False,
                        ),
                    )
                    events = [
                        event
                        async for event in agent.reply_stream(
                            UserMsg(name="user", content="Run the tool."),
                        )
                    ]
                    reply_id = agent.state.reply_id
                    expected_metadata = {
                        "trace_id": "trace",
                        "source": "tool",
                    }
                    base = {"metadata": {}}
                    self.assertEqual(
                        [
                            event.model_dump(exclude={"id", "created_at"})
                            for event in events
                            if isinstance(
                                event,
                                (
                                    CustomEvent,
                                    ToolResultStartEvent,
                                    ToolResultTextDeltaEvent,
                                    ToolResultEndEvent,
                                ),
                            )
                        ],
                        [
                            {
                                **base,
                                "type": "TOOL_RESULT_START",
                                "reply_id": reply_id,
                                "tool_call_id": "call-1",
                                "tool_call_name": tool.name,
                            },
                            *[
                                {
                                    **base,
                                    "type": "CUSTOM",
                                    "name": "mcp_progress",
                                    "value": {
                                        **update,
                                        "reply_id": reply_id,
                                        "tool_call_id": "call-1",
                                    },
                                }
                                for update in expected_progress
                            ],
                            {
                                **base,
                                "type": "TOOL_RESULT_TEXT_DELTA",
                                "reply_id": reply_id,
                                "tool_call_id": "call-1",
                                "delta": "done",
                            },
                            {
                                "type": "TOOL_RESULT_END",
                                "reply_id": reply_id,
                                "tool_call_id": "call-1",
                                "state": "success",
                                "metadata": expected_metadata,
                            },
                        ],
                    )
                    results = [
                        block
                        for msg in agent.state.context
                        for block in msg.content
                        if block.type == "tool_result"
                    ]
                    self.assertEqual(
                        [block.model_dump() for block in results],
                        [
                            {
                                "type": "tool_result",
                                "id": "call-1",
                                "name": tool.name,
                                "state": "success",
                                "created_at": AnyString(),
                                "finished_at": None,
                                "metadata": expected_metadata,
                                "output": [
                                    {
                                        "type": "text",
                                        "id": AnyString(),
                                        "created_at": AnyString(),
                                        "finished_at": None,
                                        "text": "done",
                                    },
                                ],
                            },
                        ],
                    )

    async def test_terminal_responses_do_not_accumulate_progress(self) -> None:
        """Failure and interruption cannot retain transient progress data."""
        for error in (RuntimeError("failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                client = _ProgressClient()

                async def _invoke(
                    *_args: Any,
                    call_error: BaseException = error,
                    **kwargs: Any,
                ) -> Any:
                    await kwargs["progress_callback"](1.0, None, "working")
                    raise call_error

                client.call_tool = AsyncMock(side_effect=_invoke)
                tool = _make_tool(client)
                toolkit = Toolkit(tools=[tool])
                chunks = [
                    chunk
                    async for chunk in toolkit.call_tool(
                        ToolCallBlock(
                            id="call-1",
                            name=tool.name,
                            input='{"value": 3}',
                        ),
                        AgentState(),
                    )
                ]
                self.assertEqual(
                    [chunk.metadata for chunk in chunks],
                    [
                        {
                            "mcp_progress": {
                                "progress": 1.0,
                                "total": None,
                                "message": "working",
                            },
                        },
                        {},
                        {},
                    ],
                )
                self.assertEqual(
                    chunks[-1].state,
                    (
                        ToolResultState.ERROR
                        if isinstance(error, RuntimeError)
                        else ToolResultState.INTERRUPTED
                    ),
                )

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
                "metadata": {},
                "call": {
                    "name": "work",
                    "arguments": {"value": 3},
                    "read_timeout_seconds": 7.0,
                },
            },
        )

    async def test_calls_preserve_middleware_interception(self) -> None:
        """Local MCP tools respect middleware with or without progress."""
        expected = ToolChunk(content=[TextBlock(text="blocked")])

        class _Block(ToolMiddlewareBase):
            """Return a result without calling the upstream tool."""

            async def on_tool_call(  # pylint: disable=unused-argument
                self,
                tool: Any,
                input_kwargs: Any,
                next_handler: Any,
            ) -> Any:
                """Short-circuit execution with the prepared result."""
                yield expected

        sdk = _ProgressClient()
        tool = _make_tool(sdk)
        for stream_progress in (False, True):
            tool.stream_progress = stream_progress
            tool._middlewares = [_Block()]
            chunks = [chunk async for chunk in await tool(value=3)]
            self.assertEqual(
                [chunk.model_dump() for chunk in chunks],
                [expected.model_dump()],
            )
        self.assertEqual(sdk.call, {})

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

    async def test_resource_link_without_mime_type_uses_fallback(self) -> None:
        """A valid MCP resource link may omit its MIME type."""
        tool = _make_tool(_ResourceLinkClient(), stream_progress=False)

        result = await tool(value=3)

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
                    local = await _make_tool(client, stream_progress=False)()
                    state = _State()
                    state.clients[("", "")] = {
                        "remote": _RegisteredClient(client),
                    }
                    with TestClient(_build_app(state)) as http:
                        response = http.post(
                            "/mcps/remote/tools/work",
                            json={"arguments": {}},
                        )
                    self.assertEqual(response.status_code, 200)
                    gateway = ToolChunk.model_validate(
                        response.json()["chunk"],
                    )
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

    async def test_gateway_ignores_progress_setting(self) -> None:
        """Sandbox tools make one request and always return the final chunk."""
        final = ToolChunk(content=[TextBlock(text="done")])
        for setting in (False, True):
            with self.subTest(stream_progress=setting):
                gateway = GatewayClient(
                    backend=object(),  # type: ignore[arg-type]
                    gateway_port=5600,
                )
                proxy = gateway.make_client(
                    {
                        "name": "remote",
                        "is_stateful": True,
                        "stream_progress": setting,
                        "mcp_config": {
                            "type": "http_mcp",
                            "url": "https://example.com/mcp",
                        },
                    },
                    agent_id="agent",
                    session_id="session",
                    connected=True,
                )
                facade = _GatewayFacade(final)
                proxy._gateway = facade
                proxy._cached_tools = [Tool(name="work", input_schema={})]
                tool = await proxy.get_tool("work")
                result = await tool(value=3)
                self.assertEqual(result.model_dump(), final.model_dump())
                self.assertEqual(
                    facade.requests,
                    [
                        (
                            "POST",
                            "/mcps/remote/tools/work",
                            {
                                "params": {
                                    "agent_id": "agent",
                                    "session_id": "session",
                                },
                                "body": {"arguments": {"value": 3}},
                            },
                        ),
                    ],
                )

    async def test_anyio_cancellation_preserves_sdk_cleanup(self) -> None:
        """Finish SDK cleanup before propagating an AnyIO cancellation."""
        for stream_progress, connection in product(
            (False, True),
            ("session", "client", "transport"),
        ):
            with (
                self.subTest(
                    stream_progress=stream_progress,
                    connection=connection,
                ),
                patch(
                    "agentscope.tool._adapters.mcp.ClientSession",
                ) as session_class,
                warnings.catch_warnings(record=True) as caught,
            ):
                warnings.simplefilter("always", DeprecationWarning)
                client = _CleanupProgressClient()
                tool = _make_tool(client, stream_progress=stream_progress)
                session = AsyncMock()
                session.__aenter__.return_value = session
                session.call_tool.side_effect = client.call_tool
                session_class.return_value = session
                transport = AsyncMock()
                transport.__aenter__.return_value = (
                    (object(), object())
                    if connection == "transport"
                    else session
                )
                if connection != "session":
                    tool._session = None
                    tool._client_gen = Mock(return_value=transport)
                async with anyio.create_task_group() as tasks:
                    with anyio.CancelScope() as scope:

                        async def cancel_call(
                            started: asyncio.Event,
                            cancel_scope: anyio.CancelScope,
                        ) -> None:
                            """Cancel only after the SDK call has started."""
                            await started.wait()
                            cancel_scope.cancel()

                        tasks.start_soon(cancel_call, client.started, scope)
                        if stream_progress:
                            stream = await tool(value=3)
                            async for _ in stream:
                                pass
                        else:
                            await tool(value=3)

                self.assertEqual(
                    {
                        "call_cancelled": client.cancelled.is_set(),
                        "cleanup_completed": client.cleanup_completed,
                        "cancel_propagated": scope.cancelled_caught,
                    },
                    {
                        "call_cancelled": True,
                        "cleanup_completed": True,
                        "cancel_propagated": True,
                    },
                )
                self.assertEqual(
                    [warning.category for warning in caught],
                    [DeprecationWarning] if connection == "transport" else [],
                )
                if connection != "session":
                    transport.__aexit__.assert_awaited_once()
                if connection == "transport":
                    session.__aexit__.assert_awaited_once()

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


class GatewayResultRouteTest(unittest.TestCase):
    """The in-workspace gateway returns only final tool results."""

    def test_gateway_routes_preserve_structured_results(self) -> None:
        """The final-result route retains structured-only and mixed results."""
        structured = {"city": "Paris", "temperature_c": 21.5}
        text = {
            "type": "text",
            "text": '{"city": "Paris", "temperature_c": 21.5}',
        }
        cases = [
            (
                CallToolResult(content=[], structured_content=structured),
                [text],
            ),
            (
                CallToolResult(content=[], structured_content={}),
                [
                    {"type": "text", "text": "{}"},
                ],
            ),
            (
                CallToolResult(content=[], structured_content={"city": "北京"}),
                [
                    {"type": "text", "text": '{"city": "北京"}'},
                ],
            ),
            (
                CallToolResult(
                    content=[TextContent(type="text", text="existing")],
                    structured_content=structured,
                ),
                [{"type": "text", "text": "existing"}, text],
            ),
            (
                CallToolResult(
                    content=[
                        ImageContent(
                            type="image",
                            data="aW1hZ2U=",
                            mime_type="image/png",
                        ),
                    ],
                    structured_content=structured,
                ),
                [
                    {
                        "type": "data",
                        "source": {
                            "type": "base64",
                            "data": "aW1hZ2U=",
                            "media_type": "image/png",
                        },
                        "name": None,
                    },
                    text,
                ],
            ),
            (
                CallToolResult(
                    content=[],
                    structured_content={"error": "unknown city"},
                    is_error=True,
                ),
                [{"type": "text", "text": '{"error": "unknown city"}'}],
            ),
            (CallToolResult(content=[]), []),
        ]
        for result, expected_content in cases:
            with self.subTest(result=result):
                state = _State()
                registered = _RegisteredClient(_ProgressClient())
                state.clients[("agent", "session")] = {
                    "remote": registered,  # type: ignore[dict-item]
                }
                params = {"agent_id": "agent", "session_id": "session"}
                expected = {
                    "content": expected_content,
                    "state": "error" if result.is_error else "running",
                    "is_last": True,
                    "metadata": {},
                }
                with (
                    patch.object(
                        registered.client,
                        "call_tool",
                        new=AsyncMock(return_value=result),
                    ),
                    TestClient(_build_app(state)) as client,
                ):
                    direct = client.post(
                        "/mcps/remote/tools/work",
                        params=params,
                        json={"arguments": {}},
                    )
                    self.assertEqual(
                        (
                            direct.status_code,
                            {
                                "chunk": _chunk_view(
                                    ToolChunk.model_validate(
                                        direct.json()["chunk"],
                                    ),
                                ),
                            },
                        ),
                        (200, {"chunk": expected}),
                    )

    def test_gateway_discards_progress_when_enabled_on_client(self) -> None:
        """A streaming client still yields a final-only gateway response."""
        sdk = _ProgressClient()
        state = _State()
        tool = _make_tool(sdk, stream_progress=True)
        registered = _RegisteredClient(sdk)
        state.clients[("", "")] = {"remote": registered}
        with (
            patch.object(registered, "get_tool", AsyncMock(return_value=tool)),
            TestClient(_build_app(state)) as client,
        ):
            response = client.post(
                "/mcps/remote/tools/work",
                json={"arguments": {"value": 3}},
            )
        self.assertEqual(response.status_code, 200)
        result = ToolChunk.model_validate(response.json()["chunk"])
        self.assertEqual(
            _chunk_view(result),
            {
                "content": [{"type": "text", "text": "done"}],
                "state": ToolResultState.RUNNING,
                "is_last": True,
                "metadata": {},
            },
        )
