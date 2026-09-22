# -*- coding: utf-8 -*-
"""Tests for MCP client tool pagination."""
from contextlib import asynccontextmanager
from unittest.async_case import IsolatedAsyncioTestCase

from mcp.types import ListToolsResult, Tool

from agentscope.mcp import HttpMCPConfig, MCPClient


class _PaginatedSession:
    def __init__(self) -> None:
        self.cursors: list[str | None] = []

    async def initialize(self) -> None:
        pass

    async def list_tools(self, cursor: str | None = None) -> ListToolsResult:
        self.cursors.append(cursor)
        if cursor is None:
            return ListToolsResult(
                tools=[Tool(name="first", description="", inputSchema={})],
                nextCursor="page-2",
            )
        return ListToolsResult(
            tools=[Tool(name="second", description="", inputSchema={})],
        )


class MCPClientPaginationTest(IsolatedAsyncioTestCase):
    def _client(self, is_stateful: bool) -> MCPClient:
        return MCPClient(
            name="pagination",
            is_stateful=is_stateful,
            mcp_config=HttpMCPConfig(type="http_mcp", url="http://example.com"),
        )

    async def test_stateful_client_lists_all_pages(self) -> None:
        client = self._client(is_stateful=True)
        session = _PaginatedSession()
        client._is_connected = True
        client._session = session

        tools = await client.list_raw_tools()

        self.assertEqual([tool.name for tool in tools], ["first", "second"])
        self.assertEqual(session.cursors, [None, "page-2"])

    async def test_stateless_client_lists_all_pages(self) -> None:
        client = self._client(is_stateful=False)
        session = _PaginatedSession()

        @asynccontextmanager
        async def client_generator():
            yield (object(), object())

        client._get_client_gen = client_generator

        class _ClientSession:
            def __init__(self, *args):
                pass

            async def __aenter__(self):
                return session

            async def __aexit__(self, *args):
                return None

        import agentscope.mcp._mcp_client as mcp_client_module

        previous = mcp_client_module.ClientSession
        mcp_client_module.ClientSession = _ClientSession
        try:
            tools = await client.list_raw_tools()
        finally:
            mcp_client_module.ClientSession = previous

        self.assertEqual([tool.name for tool in tools], ["first", "second"])
        self.assertEqual(session.cursors, [None, "page-2"])
