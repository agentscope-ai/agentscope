# -*- coding: utf-8 -*-
"""In-flight tool discovery must not undo a list-changed notification."""
# pylint: disable=protected-access
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import mcp.types
from mcp_client_list_changed_test import _FakeClient, _FakeTransport

from agentscope.mcp import HttpMCPConfig, MCPClient, StdioMCPConfig


class MCPClientListGenerationTest(IsolatedAsyncioTestCase):
    """Exercise both connection modes at the notification/response boundary."""

    async def test_discovery_crossing_notification_uses_new_catalog(
        self,
    ) -> None:
        """A late response cannot hide an added tool or restore an old one."""
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
            """Return the snapshot taken before a delivered notification."""

            async def __aenter__(self) -> "RacingClient":
                # A change before tools/list starts does not stale its result.
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
