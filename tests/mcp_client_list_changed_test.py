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
        self.assertEqual(len(_FakeClient.instances), 1)
        return client, _FakeClient.instances[0]

    async def test_sdk_client_receives_message_handler(self) -> None:
        """The SDK client must be built with a message handler."""
        _, fake = await self._connect()
        self.assertIsNotNone(fake.message_handler)

    async def test_tools_list_changed_invalidates_cached_tools(self) -> None:
        """A tools/list_changed notification must drop cached tools."""
        client, fake = await self._connect()

        await client.list_raw_tools()
        self.assertIsNotNone(client._cached_tools)
        self.assertEqual(fake.list_calls, 1)

        await fake.message_handler(
            mcp.types.ToolListChangedNotification(
                method="notifications/tools/list_changed",
            ),
        )
        self.assertIsNone(client._cached_tools)

    async def test_get_tool_rediscovers_after_notification(self) -> None:
        """A tool added after the notification must be discoverable."""
        client, fake = await self._connect()

        await client.list_raw_tools()
        with self.assertRaises(ValueError):
            await client.get_tool("echo_v2")

        fake.tool_names = ["echo", "echo_v2"]
        await fake.message_handler(
            mcp.types.ToolListChangedNotification(
                method="notifications/tools/list_changed",
            ),
        )
        tool = await client.get_tool("echo_v2")
        self.assertEqual(tool.mcp_name, "list_changed")
        self.assertEqual(fake.list_calls, 2)

    async def test_unrelated_notifications_keep_cached_tools(self) -> None:
        """Notifications other than tools/list_changed keep the cache."""
        client, fake = await self._connect()

        await client.list_raw_tools()
        cached = client._cached_tools

        await fake.message_handler(
            mcp.types.LoggingMessageNotification(
                method="notifications/message",
                params=mcp.types.LoggingMessageNotificationParams(
                    level="info",
                    data="unrelated",
                ),
            ),
        )
        self.assertIs(client._cached_tools, cached)
