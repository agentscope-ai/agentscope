# -*- coding: utf-8 -*-
"""Tests for ``notifications/tools/list_changed`` refreshing the tool cache."""
from types import SimpleNamespace, TracebackType
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

import mcp.types

from agentscope.mcp import MCPClient, StdioMCPConfig


class _FakeTransport:
    """Minimal transport context manager."""

    def __init__(self) -> None:
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self) -> tuple[object, object]:
        self.enter_count += 1
        return object(), object()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.exit_count += 1
        return False


class _FakeClient:
    """MCP 2.x Client stand-in that serves scripted tool listings."""

    def __init__(
        self,
        transport: _FakeTransport,
        *,
        mode: str,
        message_handler: Any = None,
    ) -> None:
        self.transport = transport
        self.mode = mode
        self.message_handler = message_handler
        self.listings: list[list[mcp.types.Tool]] = []
        self.list_calls = 0

    def script_listings(self, *listings: list[mcp.types.Tool]) -> None:
        """Serve one listing per ``list_tools`` call, holding the last."""
        self.listings = list(listings)

    async def deliver_tool_list_changed(self) -> None:
        """Simulate the server announcing ``tools/list_changed``."""
        assert self.message_handler is not None, "message_handler not wired"
        await self.message_handler(
            mcp.types.ToolListChangedNotification(
                method="notifications/tools/list_changed",
            )
        )

    async def connect(self) -> None:
        return None

    async def __aenter__(self) -> "_FakeClient":
        await self.transport.__aenter__()
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        await self.transport.__aexit__(exc_type, exc, traceback)
        return False

    async def list_tools(self, cache_mode: str = "default"):
        self.list_calls += 1
        index = min(self.list_calls, len(self.listings)) - 1
        return SimpleNamespace(tools=self.listings[index])


def _tool(name: str) -> mcp.types.Tool:
    return mcp.types.Tool(name=name, inputSchema={"type": "object"})


class MCPClientListChangedTest(IsolatedAsyncioTestCase):
    """A stateful connection must honor the server's toolset changes."""

    async def test_list_changed_notification_refreshes_stateful_cache(self):
        """The cached tool view is dropped when the notification arrives."""
        transport = _FakeTransport()
        created: list[_FakeClient] = []

        def make_client(
            transport_arg: _FakeTransport,
            *,
            mode: str,
            message_handler: Any = None,
        ) -> _FakeClient:
            fake = _FakeClient(
                transport_arg,
                mode=mode,
                message_handler=message_handler,
            )
            created.append(fake)
            return fake

        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            return_value=transport,
        ), patch(
            "agentscope.mcp._mcp_client.SDKClient",
            side_effect=make_client,
        ):
            client = MCPClient(
                name="dynamic_tools",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )
            await client.connect()
            try:
                fake = created[0]
                # The fix wires a message handler into the SDK client so the
                # notification is observed at all.
                self.assertIsNotNone(fake.message_handler)
                fake.script_listings(
                    [_tool("old_tool")],
                    [_tool("old_tool"), _tool("new_tool")],
                )

                # ``list_raw_tools`` re-resolves with cache_mode="refresh"
                # on every call; ``get_tool`` is the consumer that serves the
                # frozen cache. Before the announcement the new tool is
                # unknown to it no matter how often the server is asked.
                with self.assertRaises(ValueError):
                    await client.get_tool("new_tool")

                await fake.deliver_tool_list_changed()

                self.assertEqual(
                    [tool.name for tool in await client.list_raw_tools()],
                    ["old_tool", "new_tool"],
                )
                refreshed = await client.get_tool("new_tool")
                self.assertIsNotNone(refreshed)
                self.assertIn(
                    "new_tool",
                    [tool.name for tool in client._cached_tools or []],
                )
            finally:
                await client.close()

    async def test_unrelated_notification_keeps_stateful_cache(self):
        """Only ``tools/list_changed`` drops the cached listing."""
        transport = _FakeTransport()
        created: list[_FakeClient] = []

        def make_client(
            transport_arg: _FakeTransport,
            *,
            mode: str,
            message_handler: Any = None,
        ) -> _FakeClient:
            fake = _FakeClient(
                transport_arg,
                mode=mode,
                message_handler=message_handler,
            )
            created.append(fake)
            return fake

        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            return_value=transport,
        ), patch(
            "agentscope.mcp._mcp_client.SDKClient",
            side_effect=make_client,
        ):
            client = MCPClient(
                name="stable_tools",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )
            await client.connect()
            try:
                fake = created[0]
                fake.script_listings([_tool("kept_tool")])
                await client.list_raw_tools()

                await fake.message_handler(
                    mcp.types.PromptListChangedNotification(
                        method="notifications/prompts/list_changed",
                    )
                )
                self.assertIsNotNone(client._cached_tools)
                self.assertEqual(
                    [tool.name for tool in await client.list_raw_tools()],
                    ["kept_tool"],
                )
            finally:
                await client.close()
