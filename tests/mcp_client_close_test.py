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
        """Initialize with the shared ordering list."""
        self.order = order

    async def __aenter__(self) -> tuple[object, object]:
        """Enter the transport context, returning dummy stream pair."""
        return object(), object()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Record transport exit and suppress no exceptions."""
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
        """Initialize with shared state for coordinating test flow."""
        self.order = order
        self.call_started = call_started
        self.release_call = release_call

    async def __aenter__(self) -> "_BlockingSession":
        """Enter the session context."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Exit the session context, suppressing no exceptions."""
        return False

    async def initialize(self) -> None:
        """No-op session initializer."""
        return None

    async def list_tools(self) -> Any:
        """Return a fixed one-tool list without hitting the network."""
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
        """Signal that the call started, block until released, then return."""
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
        """Instantiate a _BlockingSession with the captured test state."""
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

    async def test_close_is_cancellation_safe(self) -> None:
        """Cancelling the close() task must not leave the client broken.

        If the outer task is cancelled while close() is waiting for
        in-flight calls to drain, the cleanup (stack teardown + state
        reset) must still complete so the client is not left with
        _is_connected=True and _is_closing=True permanently.
        """
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
                name="cancel_safe_close",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )
            await client.connect()

            tool = await client.get_tool("demo")
            call_task = asyncio.create_task(tool.call())
            await call_started.wait()

            close_task = asyncio.create_task(client.close())
            # Let close() reach the _calls_idle.wait() drain point.
            await asyncio.sleep(0.05)
            self.assertFalse(close_task.done())

            # Cancel the outer close() task while the tool call is still
            # in flight. The shield inside close() must ensure _do_close()
            # finishes even though the outer task was cancelled.
            close_task.cancel()

            # Release the blocked tool call so _do_close() can proceed.
            release_call.set()
            await asyncio.wait_for(call_task, 1)

            # Wait for _do_close() to finish in the background (shield).
            await asyncio.sleep(0.1)

            # The client must be fully closed: not connected, not closing.
            self.assertFalse(client.is_connected)
            self.assertFalse(client._is_closing)
            # Transport must have been torn down exactly once.
            self.assertIn("transport_exited", order)

    async def test_stale_tool_rejected_after_reconnect(self) -> None:
        """A tool vended from connection #1 must fail after close+reconnect.

        After close() + connect(), the client is on generation N+1. Any
        MCPTool that captured generation N must raise RuntimeError rather
        than silently calling through the old (now-closed) session.
        """
        order: list[str] = []
        call_started = asyncio.Event()
        release_call = asyncio.Event()
        release_call.set()  # not blocking for this test
        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            return_value=_RecordingTransport(order),
        ), patch(
            "agentscope.mcp._mcp_client.ClientSession",
            _session_factory(order, call_started, release_call),
        ):
            client = MCPClient(
                name="stale_tool",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )

            # Connection #1 — vend a tool.
            await client.connect()
            stale_tool = await client.get_tool("demo")
            gen_after_first_connect = client._connection_gen

            # Close and reconnect (connection #2).
            await client.close()
            await client.connect()
            self.assertGreater(
                client._connection_gen,
                gen_after_first_connect,
                "connection_gen must increase after reconnect",
            )

            # The tool from connection #1 must now be rejected.
            with self.assertRaisesRegex(RuntimeError, "stale"):
                await stale_tool.call()

            # A freshly vended tool must work.
            fresh_tool = await client.get_tool("demo")
            await asyncio.wait_for(fresh_tool.call(), 1)

            await client.close()
