# -*- coding: utf-8 -*-
"""Tests for closing stateful MCP clients with in-flight session calls."""
import asyncio
from types import SimpleNamespace, TracebackType
from typing import Any, Callable
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

import mcp.types

from agentscope.mcp import HttpMCPConfig, MCPClient, StdioMCPConfig


class _RecordingTransport:
    """Minimal transport context manager that records its exit."""

    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def __aenter__(self) -> tuple[object, object]:
        return object(), object()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.order.append("transport_exited")
        return False


class _BlockingSession:
    """ClientSession stand-in with controllable blocking calls."""

    def __init__(
        self,
        read_stream: object,
        write_stream: object,
        order: list[str],
        call_started: asyncio.Event,
        release_call: asyncio.Event,
    ) -> None:
        self.order = order
        self.call_started = call_started
        self.release_call = release_call

    async def __aenter__(self) -> "_BlockingSession":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False

    async def initialize(self) -> None:
        return None

    async def list_tools(self) -> Any:
        return SimpleNamespace(
            tools=[
                mcp.types.Tool(
                    name="demo",
                    inputSchema={"type": "object"},
                    description="",
                ),
            ],
        )

    async def call_tool(
        self,
        name: str,
        arguments: dict | None = None,
        read_timeout_seconds: Any = None,
    ) -> Any:
        self.call_started.set()
        await self.release_call.wait()
        self.order.append("call_done")
        return SimpleNamespace(content=[], isError=False)


def _session_factory(
    order: list[str],
    call_started: asyncio.Event,
    release_call: asyncio.Event,
) -> Callable[[object, object], _BlockingSession]:
    """Create a ClientSession factory bound to the per-test state."""

    def factory(read_stream: object, write_stream: object) -> _BlockingSession:
        return _BlockingSession(
            read_stream,
            write_stream,
            order,
            call_started,
            release_call,
        )

    return factory


class MCPClientCloseTest(IsolatedAsyncioTestCase):
    """Closing a stateful MCP client must wait for in-flight session calls."""

    async def test_close_waits_for_inflight_tool_call(self) -> None:
        """close() must not tear down the transport mid tool call."""
        order: list[str] = []
        call_started = asyncio.Event()
        release_call = asyncio.Event()
        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            return_value=_RecordingTransport(order),
        ), patch(
            "agentscope.mcp._mcp_client.ClientSession",
            _session_factory(order, call_started, release_call),
        ):
            client = MCPClient(
                name="close_waits",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )
            await client.connect()

            tool = await client.get_tool("demo")
            call_task = asyncio.create_task(tool.call())
            await call_started.wait()

            close_task = asyncio.create_task(client.close())
            # The close must wait for the in-flight call instead of
            # completing while the call is still running.
            await asyncio.sleep(0.05)
            self.assertFalse(close_task.done())

            release_call.set()
            await asyncio.wait_for(call_task, 1)
            await asyncio.wait_for(close_task, 1)

        self.assertEqual(order, ["call_done", "transport_exited"])

    async def test_call_started_while_closing_is_rejected(self) -> None:
        """A tool call that starts after close() must fail fast."""
        order: list[str] = []
        call_started = asyncio.Event()
        release_call = asyncio.Event()
        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            return_value=_RecordingTransport(order),
        ), patch(
            "agentscope.mcp._mcp_client.ClientSession",
            _session_factory(order, call_started, release_call),
        ):
            client = MCPClient(
                name="closing_rejects",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )
            await client.connect()

            tool = await client.get_tool("demo")
            blocked_call = asyncio.create_task(tool.call())
            await call_started.wait()

            close_task = asyncio.create_task(client.close())
            await asyncio.sleep(0.05)
            self.assertFalse(close_task.done())

            rejected_call = asyncio.create_task(tool.call())
            with self.assertRaisesRegex(RuntimeError, "closed or closing"):
                await asyncio.wait_for(rejected_call, 1)

            release_call.set()
            await asyncio.wait_for(blocked_call, 1)
            await asyncio.wait_for(close_task, 1)

    async def test_close_waits_for_inflight_list_tools(self) -> None:
        """close() must also wait for the client's own session calls."""
        order: list[str] = []
        call_started = asyncio.Event()
        release_call = asyncio.Event()

        class _BlockingListSession(_BlockingSession):
            """Block list_tools instead of call_tool."""

            async def list_tools(self) -> Any:
                self.call_started.set()
                await self.release_call.wait()
                self.order.append("list_done")
                return SimpleNamespace(tools=[])

        def list_factory(
            read_stream: object,
            write_stream: object,
        ) -> _BlockingListSession:
            return _BlockingListSession(
                read_stream,
                write_stream,
                order,
                call_started,
                release_call,
            )

        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            return_value=_RecordingTransport(order),
        ), patch(
            "agentscope.mcp._mcp_client.ClientSession",
            list_factory,
        ):
            client = MCPClient(
                name="close_waits_list",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )
            await client.connect()

            list_task = asyncio.create_task(client.list_raw_tools())
            await call_started.wait()

            close_task = asyncio.create_task(client.close())
            await asyncio.sleep(0.05)
            self.assertFalse(close_task.done())

            release_call.set()
            await asyncio.wait_for(list_task, 1)
            await asyncio.wait_for(close_task, 1)

        self.assertEqual(order, ["list_done", "transport_exited"])

    async def test_close_without_inflight_calls_is_immediate(self) -> None:
        """close() must not wait when no call is in flight."""
        order: list[str] = []
        call_started = asyncio.Event()
        release_call = asyncio.Event()
        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            return_value=_RecordingTransport(order),
        ), patch(
            "agentscope.mcp._mcp_client.ClientSession",
            _session_factory(order, call_started, release_call),
        ):
            client = MCPClient(
                name="close_idle",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )
            await client.connect()
            await asyncio.wait_for(client.close(), 1)

        self.assertEqual(order, ["transport_exited"])

    async def test_http_client_close_waits_for_inflight_tool_call(self) -> None:
        """HTTP clients share the same in-flight close coordination."""
        order: list[str] = []
        call_started = asyncio.Event()
        release_call = asyncio.Event()
        with patch.object(
            MCPClient,
            "_create_http_client",
            return_value=_RecordingTransport(order),
        ), patch(
            "agentscope.mcp._mcp_client.ClientSession",
            _session_factory(order, call_started, release_call),
        ):
            client = MCPClient(
                name="close_waits_http",
                is_stateful=True,
                mcp_config=HttpMCPConfig(url="http://unused"),
            )
            await client.connect()

            tool = await client.get_tool("demo")
            call_task = asyncio.create_task(tool.call())
            await call_started.wait()

            close_task = asyncio.create_task(client.close())
            await asyncio.sleep(0.05)
            self.assertFalse(close_task.done())

            release_call.set()
            await asyncio.wait_for(call_task, 1)
            await asyncio.wait_for(close_task, 1)

        self.assertEqual(order, ["call_done", "transport_exited"])
