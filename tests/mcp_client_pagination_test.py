# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for complete and atomic MCP tool discovery."""
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, call, create_autospec, patch

from mcp import ClientSession
from mcp.types import ListToolsResult, Tool

from agentscope.mcp import HttpMCPConfig, MCPClient


class MCPClientPaginationTest(IsolatedAsyncioTestCase):
    """Both connection modes must consume every tools/list page."""

    def setUp(self) -> None:
        """Prepare distinct tool descriptors and an empty intermediate page."""
        self.tools = [
            Tool(
                name="first_tool",
                description="The first tool.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="last_tool",
                description="The last tool.",
                inputSchema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
                outputSchema={"type": "object", "properties": {}},
            ),
        ]
        self.pages = [
            ListToolsResult(tools=self.tools[:1], nextCursor="opaque/page-2"),
            ListToolsResult(tools=[], nextCursor="opaque/page-3"),
            ListToolsResult(tools=self.tools[1:]),
        ]
        self.calls = [
            call(),
            call(cursor="opaque/page-2"),
            call(cursor="opaque/page-3"),
        ]

    @asynccontextmanager
    async def _client(
        self,
        stateful: bool,
    ) -> AsyncGenerator[tuple[MCPClient, AsyncMock], None]:
        """Create a client with one SDK-shaped session per listing."""
        session = create_autospec(ClientSession, instance=True)
        session.__aenter__.return_value = session
        session.list_tools.side_effect = self.pages
        transport = AsyncMock()
        transport.__aenter__.return_value = (object(), object())
        with patch.object(
            MCPClient,
            "_create_http_client",
            return_value=transport,
        ) as create_transport, patch(
            "agentscope.mcp._mcp_client.ClientSession",
            return_value=session,
        ) as create_session:
            client = MCPClient(
                name="paged",
                is_stateful=stateful,
                mcp_config=HttpMCPConfig(url="http://unused/mcp"),
            )
            if stateful:
                await client.connect()
            try:
                yield client, session
            finally:
                if stateful:
                    await client.close(ignore_errors=False)
            # All pages in one listing must use the same initialized session.
            self.assertEqual(
                create_session.call_count,
                create_transport.call_count,
            )
            self.assertEqual(
                session.initialize.await_count,
                create_session.call_count,
            )
            self.assertEqual(
                transport.__aexit__.await_count,
                create_transport.call_count,
            )
            self.assertEqual(
                session.__aexit__.await_count,
                create_session.call_count,
            )

    async def test_all_pages_are_cached_in_order(self) -> None:
        """An empty intermediate page must not terminate discovery."""
        for stateful in (True, False):
            with self.subTest(stateful=stateful):
                async with self._client(stateful) as (client, session):
                    self.assertEqual(await client.list_raw_tools(), self.tools)
                    self.assertEqual(client._cached_tools, self.tools)
                    self.assertEqual(
                        session.list_tools.await_args_list,
                        self.calls,
                    )
                    session.initialize.assert_awaited_once_with()
                    self.assertEqual(self.pages[0].tools, self.tools[:1])

    async def test_filters_apply_after_pagination(self) -> None:
        """Filtering must retain later tools and leave the cache complete."""
        for stateful in (True, False):
            for enabled, disabled in (
                (["last_tool"], None),
                (None, ["first_tool"]),
                (["last_tool"], ["first_tool"]),
            ):
                with self.subTest(
                    stateful=stateful,
                    enabled=enabled,
                    disabled=disabled,
                ):
                    async with self._client(stateful) as (client, session):
                        client.enable_tools = enabled
                        client.disable_tools = disabled
                        wrapped = await client.list_tools()
                        self.assertEqual(
                            [tool._tool for tool in wrapped],
                            self.tools[1:],
                        )
                        self.assertEqual(client._cached_tools, self.tools)
                        hidden = await client.get_tool("first_tool")
                        self.assertEqual(hidden._tool, self.tools[0])
                        self.assertEqual(
                            session.list_tools.await_args_list,
                            self.calls,
                        )
                        session.initialize.assert_awaited_once_with()

    async def test_get_tool_fetches_later_pages_without_a_cache(self) -> None:
        """Direct lookup must discover a tool beyond the first page."""
        for stateful in (True, False):
            with self.subTest(stateful=stateful):
                async with self._client(stateful) as (client, session):
                    tool = await client.get_tool("last_tool")
                    self.assertEqual(tool._tool, self.tools[1])
                    self.assertEqual(client._cached_tools, self.tools)
                    self.assertEqual(
                        session.list_tools.await_args_list,
                        self.calls,
                    )

    async def test_failed_page_preserves_cache_and_allows_retry(self) -> None:
        """Failed discovery must never publish a partial tool list."""
        for stateful in (True, False):
            for cached in (None, self.tools[1:]):
                with self.subTest(stateful=stateful, cached=cached):
                    async with self._client(stateful) as (client, session):
                        client._cached_tools = cached
                        expected_cache = (
                            None if cached is None else list(cached)
                        )
                        session.list_tools.side_effect = [
                            self.pages[0],
                            RuntimeError("second page failed"),
                        ]
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "second page failed",
                        ):
                            await client.list_raw_tools()
                        self.assertIs(client._cached_tools, cached)
                        self.assertEqual(client._cached_tools, expected_cache)
                        self.assertEqual(
                            session.list_tools.await_args_list,
                            self.calls[:2],
                        )
                        session.list_tools.side_effect = self.pages
                        self.assertEqual(
                            await client.list_raw_tools(),
                            self.tools,
                        )
                        self.assertEqual(client._cached_tools, self.tools)
                        self.assertEqual(
                            session.list_tools.await_args_list,
                            self.calls[:2] + self.calls,
                        )
                        self.assertEqual(
                            session.initialize.await_count,
                            1 if stateful else 2,
                        )

    async def test_single_and_empty_pages_stop_without_a_cursor(self) -> None:
        """Unpaginated and empty servers still need only one request."""
        for stateful in (True, False):
            for tools in (self.tools, []):
                with self.subTest(stateful=stateful, tools=tools):
                    async with self._client(stateful) as (client, session):
                        session.list_tools.side_effect = [
                            ListToolsResult(tools=tools),
                        ]
                        self.assertEqual(await client.list_raw_tools(), tools)
                        self.assertEqual(client._cached_tools, tools)
                        session.list_tools.assert_awaited_once_with()
