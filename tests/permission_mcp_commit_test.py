# -*- coding: utf-8 -*-
"""Commit approval before unlocking and execute only the bound MCP request."""
# pylint: disable=protected-access,cell-var-from-loop
import asyncio
from contextlib import asynccontextmanager
from itertools import product
from typing import Any, AsyncIterator, Callable
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import mcp.types

from permission_review_test import _review
from realtime_agent_test import ScriptedModel
from utils import MockModel

from agentscope.agent import Agent, RealtimeAgent
from agentscope.event import ToolResultEndEvent, ToolResultStartEvent
from agentscope.mcp import HttpMCPConfig, MCPClient
from agentscope.message import ToolCallBlock
from agentscope.middleware import MiddlewareBase
from agentscope.permission import PermissionContext
from agentscope.state import AgentState
from agentscope.tool import (
    MCPTool,
    ToolBase,
    ToolChunk,
    Toolkit,
    ToolMiddlewareBase,
)
from agentscope.workspace._gateway_client import GatewayMCPTool


def _session() -> AsyncMock:
    client = AsyncMock()
    client.call_tool.return_value = mcp.types.CallToolResult(content=[])
    return client


class _Forward(ToolMiddlewareBase):
    """Exercise the real tool middleware leaf."""

    async def on_tool_call(
        self,
        tool: ToolBase,
        input_kwargs: dict,
        next_handler: Callable,
    ) -> AsyncIterator[ToolChunk]:
        async for chunk in next_handler(**input_kwargs):
            yield chunk


class MCPCommitTest(IsolatedAsyncioTestCase):
    """Exercise the actual adapter with a recording transport."""

    async def test_sdk_mismatch_retry_checks_approved_descriptor(self) -> None:
        """Exercise the modern SDK retry loop with a recording session."""
        original = mcp.types.Tool(
            name="echo",
            input_schema={"type": "object", "const": True},
        )
        for change in ("same", "schema", "typed_value", "removed"):
            with self.subTest(change=change):
                client = mcp.Client("http://localhost/mcp")
                session = _session()
                session.protocol_version = "2026-07-28"
                session.call_tool.side_effect = [
                    mcp.MCPError(mcp.types.HEADER_MISMATCH, "Refresh"),
                    mcp.types.CallToolResult(content=[]),
                ]
                client._session = session
                fresh = original.model_copy(deep=True)
                if change == "schema":
                    fresh.input_schema["required"] = ["new"]
                elif change == "typed_value":
                    fresh.input_schema["const"] = 1
                pages = [
                    mcp.types.ListToolsResult(tools=[], next_cursor="next"),
                    mcp.types.ListToolsResult(
                        tools=[] if change == "removed" else [fresh],
                    ),
                ]
                tool = MCPTool(mcp_name="demo", tool=original, session=client)
                with patch.object(
                    client,
                    "list_tools",
                    AsyncMock(side_effect=pages),
                ) as listing:
                    bound = tool.bind_for_dispatch()
                    if change == "same":
                        await bound.call()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "MCP tool"):
                            await bound.call()
                self.assertEqual(
                    (session.call_tool.await_count, listing.await_count),
                    (2 if change == "same" else 1, 2),
                )

    async def test_chat_guard_commit_orders_updates(self) -> None:
        """Submit under the guard and keep the committed route."""
        for before, stop in product((True, False), ("input", "read_only")):
            with self.subTest(before=before, stop=stop):
                original, replacement = _session(), _session()
                tool = MCPTool(
                    "editor",
                    mcp.types.Tool(name="render", input_schema={}),
                    session=original,
                    middlewares=[_Forward()],
                )
                state = AgentState(
                    permission_context=PermissionContext(
                        mode="auto",
                        interactive=False,
                    ),
                )
                call = ToolCallBlock(id="one", name=tool.name, input='{"v":1}')
                state.append_context("agent", [call])
                started = asyncio.Event()
                pending = []

                def withdraw() -> None:
                    if stop == "input":
                        pending.append("Stop")
                    else:
                        state.permission_context.read_only = True

                class Guard(MiddlewareBase):
                    """Inject a withdrawal on either side of commitment."""

                    @asynccontextmanager
                    async def tool_dispatch_guard(
                        self,
                        agent: Agent,
                    ) -> AsyncIterator[Callable[[], bool]]:
                        if before:
                            await asyncio.sleep(0)
                            withdraw()
                        yield lambda: bool(pending)
                        if not before:
                            # The consumer must see commitment before cleanup
                            # completes, while the transport is still idle.
                            await started.wait()
                            original.call_tool.assert_not_awaited()
                            withdraw()
                            tool._session = replacement
                            tool._tool.name = "other"

                agent = Agent(
                    "agent",
                    "Help",
                    MockModel(),
                    state=state,
                    toolkit=Toolkit(tools=[tool]),
                    middlewares=[Guard()],
                    permission_reviewer=AsyncMock(return_value=_review()),
                )
                ends = []
                async with asyncio.timeout(3):
                    async for event in agent._execute_tool_call(call):
                        if isinstance(event, ToolResultStartEvent):
                            started.set()
                        if isinstance(event, ToolResultEndEvent):
                            ends.append(event.state)
                calls = [
                    (c.args[0], c.kwargs["arguments"])
                    for c in original.call_tool.await_args_list
                ]
                self.assertEqual(
                    (ends, calls, replacement.call_tool.await_count),
                    (["denied"], [], 0)
                    if before
                    else (["success"], [("render", {"v": 1})], 0),
                )

    async def test_stream_uses_arguments_and_target_at_call(self) -> None:
        """Streaming defers task creation and transport entry past call()."""
        for stateful in (False, True):
            with self.subTest(stateful=stateful):
                client, other = _session(), _session()
                definition = mcp.types.Tool(name="render", input_schema={})

                @asynccontextmanager
                async def connect() -> AsyncIterator[Any]:
                    await asyncio.sleep(0)
                    definition.name = "during-connect"
                    yield client

                tool = MCPTool(
                    "editor",
                    definition,
                    stream_progress=True,
                    session=client if stateful else None,
                    client_gen=None if stateful else connect,
                )
                values = [1]
                stream = await tool.call(values=values)
                values.append(2)
                tool._session = other
                tool._client_gen = None
                definition.name = "after-call"
                async for _ in stream:
                    pass
                self.assertEqual(
                    [
                        (c.args[0], c.kwargs["arguments"])
                        for c in client.call_tool.await_args_list
                    ],
                    [("render", {"values": [1]})],
                )
                other.call_tool.assert_not_awaited()

    async def test_realtime_pins_native_connection(self) -> None:
        """Rotate live connection settings before the transport opens."""
        client = MCPClient(
            name="editor",
            is_stateful=False,
            mcp_config=HttpMCPConfig(
                url="http://original.invalid/mcp",
                headers={"X-Site": "old"},
            ),
        )
        await client.set_runtime_headers({"X-Run": "approved"})
        definition = mcp.types.Tool(name="render", input_schema={})
        client._cached_tools = [definition]
        tool = await client.get_tool("render")
        confirmation = tool.confirmation_identity()
        session = _session()
        seen = []

        @asynccontextmanager
        async def transport(url: str, http_client: Any) -> AsyncIterator[Any]:
            seen.append(
                (
                    url,
                    http_client.headers["X-Site"],
                    http_client.headers["X-Run"],
                ),
            )
            yield session

        agent = RealtimeAgent(
            "agent",
            "Help",
            ScriptedModel([[]]),
            toolkit=Toolkit(tools=[tool]),
            permission_reviewer=AsyncMock(return_value=_review()),
            state=AgentState(
                permission_context=PermissionContext(
                    mode="auto",
                    interactive=False,
                ),
            ),
        )
        call = ToolCallBlock(id="call", name=tool.name, input='{"v":1}')
        agent._reply_id = agent.state.reply_id
        agent.state.append_context("agent", [call])

        with patch(
            "agentscope.mcp._mcp_client.streamable_http_client",
            transport,
        ), patch(
            "agentscope.mcp._mcp_client.SDKClient",
            side_effect=lambda t, **kw: t,
        ):
            task = asyncio.create_task(agent._run_tool(agent._reply_id, call))
            try:
                async with asyncio.timeout(3):
                    while not isinstance(
                        (await agent._out.get()).event,
                        ToolResultStartEvent,
                    ):
                        pass
                    self.assertEqual(seen, [])
                    client.mcp_config.url = "http://replacement.invalid/mcp"
                    self.assertNotEqual(
                        confirmation,
                        tool.confirmation_identity(),
                    )
                    client.mcp_config.headers["X-Site"] = "new"
                    await client.set_runtime_headers({"X-Run": "replacement"})
                    definition.name = "replacement"
                    await task
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(
            (
                seen,
                [
                    (c.args[0], c.kwargs["arguments"])
                    for c in session.call_tool.await_args_list
                ],
            ),
            (
                [("http://original.invalid/mcp", "old", "approved")],
                [("render", {"v": 1})],
            ),
        )

    async def test_gateway_uses_committed_route(self) -> None:
        """Send the scoped request to the gateway selected at commit."""
        gateway, replacement = AsyncMock(), AsyncMock()
        gateway.exec_request.return_value = (200, '{"chunk":{"content":[]}}')
        definition = mcp.types.Tool(name="render", input_schema={})
        tool = GatewayMCPTool(
            "editor",
            definition,
            gateway,
            "agent",
            "one",
            binding="approved",
        )
        committed = tool.bind_for_dispatch()
        tool._gateway = replacement
        tool._session_id = "two"
        definition.name = "other"
        await committed(v=1)
        gateway.exec_request.assert_awaited_once_with(
            "POST",
            "/mcps/editor/approved-tools/render",
            params={"agent_id": "agent", "session_id": "one"},
            body={"arguments": {"v": 1}, "binding": "approved"},
        )
        replacement.exec_request.assert_not_awaited()

    async def test_gateway_missing_binding_stops_approved_call(self) -> None:
        """An older gateway cannot validate an approved target."""
        gateway = AsyncMock()
        definition = mcp.types.Tool(name="render", input_schema={})
        tool = GatewayMCPTool("editor", definition, gateway)
        result = await tool.bind_for_dispatch()(v=1)
        self.assertEqual(
            (result.state, result.content[0].text),
            ("error", "Gateway did not supply an approval binding."),
        )
        gateway.exec_request.assert_not_awaited()
