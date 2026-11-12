# -*- coding: utf-8 -*-
"""Tests for tool-cache invalidation on tools/list_changed notifications."""
# pylint: disable=protected-access
import asyncio
from types import TracebackType
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

import mcp.types

from agentscope.mcp import HttpMCPConfig, MCPClient, StdioMCPConfig


class _FakeTransport:
    """Minimal transport context manager for lifecycle-only tests."""

    async def __aenter__(self) -> tuple[object, object]:
        return object(), object()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False


class _FakeClient:
    """MCP 2.x Client stand-in that records its message handler."""

    instances: list["_FakeClient"] = []

    def __init__(
        self,
        transport: Any,
        *,
        mode: str,
        message_handler: Any = None,
    ) -> None:
        self.transport = transport
        self.mode = mode
        self.message_handler = message_handler
        self.list_calls = 0
        self.tool_names: list[str] = ["echo"]
        _FakeClient.instances.append(self)

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return False

    async def list_tools(self, **_kwargs: Any) -> mcp.types.ListToolsResult:
        """Serve the current tool names as raw tool descriptors."""
        self.list_calls += 1
        return mcp.types.ListToolsResult(
            tools=[
                mcp.types.Tool(name=name, inputSchema={"type": "object"})
                for name in self.tool_names
            ],
        )


class MCPClientListChangedTest(IsolatedAsyncioTestCase):
    """Server-side tool changes must surface on the next tool lookup."""

    def setUp(self) -> None:
        _FakeClient.instances = []

    async def _connect(self) -> tuple[MCPClient, _FakeClient]:
        """Connect a stateful client against the fake SDK client."""
        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            return_value=_FakeTransport(),
        ), patch(
            "agentscope.mcp._mcp_client.SDKClient",
            _FakeClient,
        ):
            client = MCPClient(
                name="list_changed",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )
            await client.connect()
            self.addAsyncCleanup(client.close)
        self.assertEqual(len(_FakeClient.instances), 1)
        fake = _FakeClient.instances[0]
        self.assertIsNotNone(fake.message_handler)
        return client, fake

    async def test_get_tool_rediscovers_after_notification(self) -> None:
        """A tool added after the notification must be discoverable."""
        client, fake = await self._connect()

        with self.assertRaises(ValueError):
            await client.get_tool("echo_v2")
        self.assertEqual(fake.list_calls, 1)

        fake.tool_names = ["echo", "echo_v2"]
        await fake.message_handler(
            mcp.types.ToolListChangedNotification(
                method="notifications/tools/list_changed",
            ),
        )
        tool = await client.get_tool("echo_v2")
        self.assertEqual(
            (tool.name, fake.list_calls),
            ("mcp__list_changed__echo_v2", 2),
        )
        self.assertEqual(
            client._cached_tools,
            [
                mcp.types.Tool(name="echo", inputSchema={"type": "object"}),
                mcp.types.Tool(name="echo_v2", inputSchema={"type": "object"}),
            ],
        )

    async def test_unrelated_notifications_keep_cached_tools(self) -> None:
        """Notifications other than tools/list_changed keep the cache."""
        client, fake = await self._connect()

        await client.get_tool("echo")
        cached = client._cached_tools
        fake.tool_names = ["echo_v2"]

        await fake.message_handler(
            mcp.types.LoggingMessageNotification(
                method="notifications/message",
                params=mcp.types.LoggingMessageNotificationParams(
                    level="info",
                    data="unrelated",
                ),
            ),
        )
        tool = await client.get_tool("echo")
        self.assertEqual(
            (tool.name, fake.list_calls),
            ("mcp__list_changed__echo", 1),
        )
        self.assertIs(client._cached_tools, cached)


class MCPClientConcurrentDiscoveryTest(IsolatedAsyncioTestCase):
    """Coordinate concurrent discovery and connection cleanup with events."""

    def setUp(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.connections: list[_FakeClient] = []
        self.closed: list[_FakeClient] = []
        self.closing = asyncio.Event()
        self.close_gate: asyncio.Event | None = None
        self.notify_on_enter = True
        self.error: Exception | None = None
        self.catalog = [
            mcp.types.Tool(name=name, inputSchema={"type": "object"})
            for name in ("echo", "hidden")
        ]
        case = self

        class ControlledClient(_FakeClient):
            """Hold a snapshot until the test releases its response."""

            async def __aenter__(self) -> "ControlledClient":
                case.connections.append(self)
                if case.notify_on_enter:
                    await self.message_handler(
                        mcp.types.ToolListChangedNotification(
                            method="notifications/tools/list_changed",
                        ),
                    )
                return self

            async def list_tools(
                self,
                **_kwargs: Any,
            ) -> mcp.types.ListToolsResult:
                snapshot = mcp.types.ListToolsResult(tools=case.catalog)
                case.started.set()
                await case.release.wait()
                if case.error is not None:
                    raise case.error
                return snapshot

            async def __aexit__(self, *args: Any) -> bool:
                if case.close_gate is not None:
                    case.closing.set()
                    await case.close_gate.wait()
                case.closed.append(self)
                return False

        self.enterContext(
            patch("agentscope.mcp._mcp_client.SDKClient", ControlledClient),
        )
        self.client = MCPClient(
            name="concurrent",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url="http://mcp.invalid/mcp"),
        )

    async def _start(
        self,
        entry: str = "list",
        name: str = "echo",
    ) -> asyncio.Task[Any]:
        """Wait until the caller has joined discovery before returning."""
        joined = asyncio.Event()

        async def lookup() -> Any:
            joined.set()
            if entry == "get":
                return await self.client.get_tool(name)
            return await self.client.list_raw_tools()

        task = asyncio.create_task(lookup())
        self.addAsyncCleanup(self._cancel, task)
        await joined.wait()
        return task

    async def _cancel(self, task: asyncio.Task[Any]) -> None:
        """Drain caller tasks even when an assertion fails."""
        if self.close_gate is not None:
            self.close_gate.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_concurrent_discovery_shares_one_connection(self) -> None:
        """Connection notifications must not multiply discovery attempts."""
        for notify in (False, True):
            for callers in (2, 8):
                with self.subTest(notify=notify, callers=callers):
                    self.notify_on_enter = notify
                    self.started.clear()
                    self.release.clear()
                    self.connections.clear()
                    self.closed.clear()
                    tasks = [await self._start() for _ in range(callers)]
                    await self.started.wait()
                    self.release.set()
                    results = await asyncio.gather(*tasks)
                    self.assertEqual(results, [self.catalog] * callers)
                    self.assertEqual(len(self.connections), 1)
                    self.assertEqual(self.closed, self.connections)
                    self.assertIsNone(self.client._discovery_task)

    async def test_get_and_list_share_unfiltered_discovery(self) -> None:
        """Filtering must not hide tools from concurrent direct lookups."""
        self.client.enable_tools = ["echo"]
        listed = await self._start()
        hidden = await self._start("get", "hidden")
        await self.started.wait()
        self.release.set()
        raw, tool = await asyncio.gather(listed, hidden)
        self.assertEqual(raw, self.catalog[:1])
        self.assertEqual(tool.name, "mcp__concurrent__hidden")
        self.assertEqual(self.client._cached_tools, self.catalog)
        self.assertEqual(len(self.connections), 1)

    async def test_connection_notifications_do_not_cause_ping_pong(
        self,
    ) -> None:
        """Sibling connection notifications must not exhaust both attempts."""
        entered = [asyncio.Event() for _ in range(4)]
        case = self

        class PingPongClient(_FakeClient):
            """Release each old response after the next connection opens."""

            index: int = 0

            async def __aenter__(self) -> "PingPongClient":
                self.index = len(case.connections)
                case.connections.append(self)
                if self.index:
                    await self.message_handler(
                        mcp.types.ToolListChangedNotification(
                            method="notifications/tools/list_changed",
                        ),
                    )
                entered[self.index].set()
                return self

            async def list_tools(
                self,
                **_kwargs: Any,
            ) -> mcp.types.ListToolsResult:
                if self.index == 0:
                    await case.release.wait()
                elif self.index < 3:
                    await entered[self.index + 1].wait()
                return mcp.types.ListToolsResult(tools=case.catalog)

        with patch("agentscope.mcp._mcp_client.SDKClient", PingPongClient):
            first = await self._start()
            await entered[0].wait()
            second = await self._start()
            self.release.set()
            results = await asyncio.wait_for(
                asyncio.gather(first, second, return_exceptions=True),
                timeout=5,
            )
        self.assertEqual(results, [self.catalog, self.catalog])
        self.assertEqual(self.client._cached_tools, self.catalog)
        self.assertEqual(len(self.connections), 1)

    async def test_other_session_notification_fences_shared_cache(
        self,
    ) -> None:
        """A notification outside discovery must still reject its snapshot."""
        callers = [await self._start() for _ in range(2)]
        await self.started.wait()
        self.catalog = [
            mcp.types.Tool(name="new", inputSchema={"type": "object"}),
        ]
        async with self.client._get_client_gen():
            self.release.set()
            results = await asyncio.gather(*callers)
        self.assertEqual(results, [self.catalog, self.catalog])
        self.assertEqual(self.client._cached_tools, self.catalog)
        self.assertEqual(len(self.connections), 3)

    async def test_cancelled_caller_does_not_cancel_other_waiters(
        self,
    ) -> None:
        """Either the first caller or a follower may cancel independently."""
        for cancelled in (0, 1):
            with self.subTest(cancelled=cancelled):
                self.started.clear()
                self.release.clear()
                self.connections.clear()
                self.closed.clear()
                callers = [await self._start() for _ in range(2)]
                await self.started.wait()
                await self._cancel(callers[cancelled])
                self.assertEqual(self.closed, [])
                self.release.set()
                result = await callers[1 - cancelled]
                self.assertEqual(result, self.catalog)
                self.assertEqual(self.closed, self.connections)
                self.assertEqual(len(self.connections), 1)
                self.assertIsNone(self.client._discovery_task)

    async def test_last_cancelled_caller_closes_connection(self) -> None:
        """Abandoned discovery must close and allow a subsequent request."""
        callers = [await self._start() for _ in range(2)]
        await self.started.wait()
        for caller in callers:
            await self._cancel(caller)
        self.assertEqual(self.closed, self.connections)
        self.assertEqual(len(self.connections), 1)
        self.assertIsNone(self.client._discovery_task)
        self.assertIsNone(self.client._cached_tools)
        self.release.set()
        self.assertEqual(await self.client.list_raw_tools(), self.catalog)
        self.assertEqual(len(self.connections), 2)

    async def test_failed_discovery_reaches_all_callers_and_can_retry(
        self,
    ) -> None:
        """A failed shared task must not poison later discovery."""
        self.error = RuntimeError("discovery failed")
        callers = [await self._start() for _ in range(2)]
        await self.started.wait()
        self.release.set()
        results = await asyncio.gather(*callers, return_exceptions=True)
        self.assertEqual(results, [self.error, self.error])
        self.assertEqual(self.closed, self.connections)
        self.assertEqual(len(self.connections), 1)
        self.assertIsNone(self.client._discovery_task)
        self.error = None
        self.assertEqual(await self.client.list_raw_tools(), self.catalog)
        self.assertEqual(len(self.connections), 2)

    async def test_cancelled_cleanup_does_not_clear_new_discovery(
        self,
    ) -> None:
        """A fresh caller may arrive while the abandoned connection closes."""
        self.close_gate = asyncio.Event()
        first = await self._start()
        await self.started.wait()
        first.cancel()
        await self.closing.wait()
        self.started.clear()
        second = await self._start()
        await self.started.wait()
        active = self.client._discovery_task
        self.close_gate.set()
        await asyncio.gather(first, return_exceptions=True)
        self.assertIs(self.client._discovery_task, active)
        self.assertFalse(second.done())
        self.release.set()
        self.assertEqual(await second, self.catalog)
        self.assertEqual(self.closed, self.connections)
        self.assertEqual(len(self.connections), 2)
        self.assertIsNone(self.client._discovery_task)


class MCPClientListGenerationTest(IsolatedAsyncioTestCase):
    """Reject tool lists invalidated during discovery."""

    async def test_discovery_crossing_notification_uses_new_catalog(
        self,
    ) -> None:
        """Retry stale responses once in both connection modes."""
        for stateful in (False, True):
            for entry in ("list", "get"):
                for repeated in (False, True):
                    with self.subTest(
                        stateful=stateful,
                        entry=entry,
                        repeated=repeated,
                    ):
                        await self._check(stateful, entry, repeated)

    async def _check(
        self,
        stateful: bool,
        entry: str,
        repeated: bool,
    ) -> None:
        calls = 0

        class RacingClient(_FakeClient):
            """Notify after taking a tool-list snapshot."""

            async def __aenter__(self) -> "RacingClient":
                # Pre-request notifications must not invalidate the response.
                await self.message_handler(
                    mcp.types.ToolListChangedNotification(
                        method="notifications/tools/list_changed",
                    ),
                )
                return self

            async def list_tools(
                self,
                **_kwargs: Any,
            ) -> mcp.types.ListToolsResult:
                nonlocal calls
                calls += 1
                if calls > 3:
                    raise AssertionError("Discovery retried without a bound")
                name = "old" if calls == 1 else "new"
                snapshot = mcp.types.ListToolsResult(
                    tools=[
                        mcp.types.Tool(
                            name=name,
                            inputSchema={"type": "object"},
                        ),
                    ],
                )
                if calls == 1 or repeated:
                    await self.message_handler(
                        mcp.types.ToolListChangedNotification(
                            method="notifications/tools/list_changed",
                        ),
                    )
                return snapshot

        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            return_value=_FakeTransport(),
        ), patch("agentscope.mcp._mcp_client.SDKClient", RacingClient):
            client = MCPClient(
                name="generation",
                is_stateful=stateful,
                mcp_config=StdioMCPConfig(command="unused")
                if stateful
                else HttpMCPConfig(url="http://mcp.invalid/mcp"),
            )
            if stateful:
                await client.connect()
            try:
                if repeated:
                    with self.assertRaisesRegex(RuntimeError, "changed"):
                        if entry == "get":
                            await client.get_tool("new")
                        else:
                            await client.list_raw_tools()
                    self.assertIsNone(client._cached_tools)
                else:
                    if entry == "list":
                        result = await client.list_raw_tools()
                        self.assertEqual(
                            [tool.name for tool in result],
                            ["new"],
                        )
                    await client.get_tool("new")
                    with self.assertRaises(ValueError):
                        await client.get_tool("old")
                self.assertEqual(calls, 2)
            finally:
                if stateful:
                    await client.close()
