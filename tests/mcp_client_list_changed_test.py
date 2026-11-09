# -*- coding: utf-8 -*-
"""Tests for tool-cache invalidation on tools/list_changed notifications."""
# pylint: disable=protected-access
from types import TracebackType
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

import mcp.types

from agentscope.mcp import MCPClient, StdioMCPConfig


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
