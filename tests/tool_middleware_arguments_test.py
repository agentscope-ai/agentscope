# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
"""Tool arguments must not control the middleware chain's internal cursor."""
import asyncio
import json
import sys
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

from mcp import Client, StdioServerParameters, stdio_client
from tool_middleware_test import _NonStreamingTool, _expected_chunk

from agentscope.message import TextBlock, ToolCallBlock, ToolResultState
from agentscope.state import AgentState
from agentscope.tool import (
    FunctionTool,
    MCPTool,
    ToolBase,
    ToolChunk,
    ToolMiddlewareBase,
    Toolkit,
    ToolResponse,
)

_INDEXES: tuple[int | str, ...] = (0, 1, 2, -1, "catalog")


def _lookup(index: int | str = "default") -> str:
    """Return the requested index, or the default when omitted."""
    return f"index={index!r}"


def _required_lookup(index: int | str) -> str:
    """Return the explicitly requested index."""
    return _lookup(index)


class _RecordingMiddleware(ToolMiddlewareBase):
    """Record inputs and onion order, optionally rewriting tool arguments."""

    def __init__(
        self,
        label: str,
        events: list,
        updates: dict | None = None,
    ) -> None:
        self.label = label
        self.events = events
        self.updates = updates or {}

    async def on_tool_call(
        self,
        tool: ToolBase,
        input_kwargs: dict,
        next_handler: Any,
    ) -> AsyncGenerator[ToolChunk, None]:
        """Capture inputs before forwarding an independent rewritten dict."""
        self.events.append((f"{self.label}-pre", dict(input_kwargs)))
        async for chunk in next_handler(**(input_kwargs | self.updates)):
            yield chunk
        self.events.append((f"{self.label}-post", dict(input_kwargs)))


class _IndexStream(_NonStreamingTool):
    """Exercise the native async-generator branch in ToolBase."""

    async def call(  # type: ignore[override]
        self,
        index: str = "default",
        **kwargs: Any,
    ) -> AsyncGenerator[ToolChunk, None]:
        """Yield two chunks identifying the requested index."""
        for suffix in ("first", "last"):
            yield ToolChunk(
                content=[TextBlock(text=f"{index}:{suffix}")],
            )


_MCP_SERVER = '''
from mcp.server import MCPServer

server = MCPServer("middleware-arguments")

@server.tool()
def lookup(index: int | str = "default") -> str:
    """Return the requested remote index."""
    return f"index={index!r}"

server.run(transport="stdio")
'''


class ToolMiddlewareArgumentsTest(IsolatedAsyncioTestCase):
    """Preserve argument names through direct, Toolkit, and MCP calls."""

    def _middlewares(self, events: list) -> list[ToolMiddlewareBase]:
        """Build two layers so a caller-controlled cursor cannot skip one."""
        return [
            _RecordingMiddleware("outer", events),
            _RecordingMiddleware("inner", events),
        ]

    def _assert_events(self, events: list, inputs: dict) -> None:
        """Require each layer to see the complete input exactly once."""
        self.assertEqual(
            events,
            [
                ("outer-pre", inputs),
                ("inner-pre", inputs),
                ("inner-post", inputs),
                ("outer-post", inputs),
            ],
        )

    async def _invoke(self, tool: ToolBase, inputs: dict) -> list[str]:
        """Invoke through Toolkit and check the final response succeeds."""
        results = [
            result
            async for result in Toolkit(tools=[tool]).call_tool(
                ToolCallBlock(
                    id="index-call",
                    name=tool.name,
                    input=json.dumps(inputs),
                ),
                AgentState(),
            )
        ]
        self.assertIsInstance(results[-1], ToolResponse)
        self.assertEqual(results[-1].state, ToolResultState.SUCCESS)
        return [block.text for block in results[-1].content]

    async def test_function_index_preserves_inputs_and_onion_order(
        self,
    ) -> None:
        """Zero, positive, negative, and string indexes remain tool inputs."""
        for function in (_lookup, _required_lookup):
            for index in _INDEXES:
                with self.subTest(function=function.__name__, index=index):
                    events: list = []
                    tool = FunctionTool(
                        function,
                        middlewares=self._middlewares(events),
                    )
                    self.assertIn("index", tool.input_schema["properties"])
                    self.assertEqual(
                        await self._invoke(tool, {"index": index}),
                        [_lookup(index)],
                    )
                    self._assert_events(events, {"index": index})

    async def test_default_and_no_middleware_remain_compatible(self) -> None:
        """Omitted arguments use defaults; unwrapped tools keep inputs."""
        events: list = []
        tool = FunctionTool(_lookup, middlewares=self._middlewares(events))
        self.assertEqual(await self._invoke(tool, {}), [_lookup()])
        self._assert_events(events, {})
        for index in _INDEXES:
            with self.subTest(index=index):
                self.assertEqual(
                    await self._invoke(
                        FunctionTool(_lookup),
                        {"index": index},
                    ),
                    [_lookup(index)],
                )

    async def test_direct_tool_preserves_the_complete_argument_dict(
        self,
    ) -> None:
        """Direct calls preserve index alongside the other tool arguments."""
        inputs = {
            "index": "catalog",
            "chain_kwargs": {"limit": 3},
            "query": "needle",
        }
        events: list = []
        tool = _NonStreamingTool(middlewares=self._middlewares(events))

        chunks = [chunk.model_dump() async for chunk in await tool(**inputs)]

        self.assertEqual(
            chunks,
            [_expected_chunk(f"call() kwargs={inputs}")],
        )
        self._assert_events(events, inputs)

    async def test_middleware_can_inject_or_rewrite_index(self) -> None:
        """Recursive forwarding must keep index out of the cursor slot."""
        input_cases: list[dict] = [{}, {"index": "original"}]
        for inputs in input_cases:
            with self.subTest(inputs=inputs):
                events: list = []
                tool = FunctionTool(
                    _lookup,
                    middlewares=[
                        _RecordingMiddleware(
                            "outer",
                            events,
                            {"index": "rewritten"},
                        ),
                        _RecordingMiddleware("inner", events),
                    ],
                )
                self.assertEqual(
                    await self._invoke(tool, inputs),
                    [_lookup("rewritten")],
                )
                self.assertEqual(
                    events,
                    [
                        ("outer-pre", inputs),
                        ("inner-pre", {"index": "rewritten"}),
                        ("inner-post", {"index": "rewritten"}),
                        ("outer-post", inputs),
                    ],
                )

    async def test_index_cannot_skip_short_circuit_middleware(self) -> None:
        """A numeric input cannot bypass a middleware that stops the call."""
        calls: list = []

        def lookup(index: int = 0) -> str:
            """Record actual tool executions."""
            calls.append(index)
            return "executed"

        class _Block(ToolMiddlewareBase):
            async def on_tool_call(
                self,
                tool: ToolBase,
                input_kwargs: dict,
                next_handler: Any,
            ) -> AsyncGenerator[ToolChunk, None]:
                """Return without delegating to the tool."""
                yield ToolChunk(content=[TextBlock(text="blocked")])

        tool = FunctionTool(lookup, middlewares=[_Block()])
        self.assertEqual(await self._invoke(tool, {"index": 1}), ["blocked"])
        self.assertEqual(calls, [])

    async def test_native_and_wrapped_async_streams_keep_index(self) -> None:
        """Both stream normalization branches preserve arguments and order."""

        async def stream(index: str) -> AsyncGenerator[str, None]:
            """Produce a stream from a FunctionTool coroutine adapter."""
            for suffix in ("first", "last"):
                yield f"{index}:{suffix}"

        for factory in (
            lambda middlewares: _IndexStream(middlewares=middlewares),
            lambda middlewares: FunctionTool(stream, middlewares=middlewares),
        ):
            events: list = []
            tool = factory(self._middlewares(events))
            with self.subTest(tool=type(tool).__name__):
                chunks = [chunk async for chunk in await tool(index="catalog")]
                self.assertEqual(
                    [
                        block.text
                        for chunk in chunks
                        for block in chunk.content
                    ],
                    ["catalog:first", "catalog:last"],
                )
                self._assert_events(events, {"index": "catalog"})

    async def test_real_stdio_mcp_keeps_remote_index(self) -> None:
        """A local MCP server receives the same arguments as middleware."""

        async def exercise() -> None:
            """Use one subprocess session without network or external tools."""
            params = StdioServerParameters(
                command=sys.executable,
                args=["-u", "-c", _MCP_SERVER],
            )
            async with Client(stdio_client(params), mode="auto") as session:
                catalog = await session.list_tools()
                events: list = []
                tool = MCPTool(
                    mcp_name="index-server",
                    tool=catalog.tools[0],
                    session=session,
                    middlewares=self._middlewares(events),
                )
                self.assertIn("index", tool.input_schema["properties"])
                for index in _INDEXES:
                    with self.subTest(index=index):
                        events.clear()
                        self.assertEqual(
                            await self._invoke(tool, {"index": index}),
                            [_lookup(index)],
                        )
                        self._assert_events(events, {"index": index})

        await asyncio.wait_for(exercise(), timeout=30)
