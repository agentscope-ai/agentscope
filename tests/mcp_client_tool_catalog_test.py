# -*- coding: utf-8 -*-
"""Tests for the tool catalog a stateful MCP client caches across a
reconnect."""
from types import TracebackType
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

import mcp.types as mcp_types

from agentscope.mcp import MCPClient, StdioMCPConfig


class _OneShotTransport:
    """Minimal transport context manager that cannot be entered twice."""

    def __init__(self) -> None:
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self) -> tuple[object, object]:
        self.enter_count += 1
        if self.enter_count > 1:
            raise AssertionError("transport context manager was reused")
        return object(), object()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.exit_count += 1
        return False


class _CatalogClient:
    """MCP client stand-in whose advertised catalog the test can swap.

    A real server can expose a different tool set after it restarts, so the
    catalog is indexed by connection rather than fixed for the whole test.
    """

    catalogs: list[list[str]] = []
    connection = 0
    instance: "_CatalogClient | None" = None

    def __init__(
        self,
        transport: _OneShotTransport,
        *,
        mode: str,
        message_handler: Any = None,
    ) -> None:
        self.transport = transport
        self.mode = mode
        self.message_handler = message_handler
        _CatalogClient.instance = self

    async def __aenter__(self) -> "_CatalogClient":
        await self.transport.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        await self.transport.__aexit__(exc_type, exc, traceback)
        return False

    async def connect(self) -> None:
        """Accept the handshake without I/O."""

    async def list_tools(  # pylint: disable=unused-argument
        self,
        cache_mode: str = "auto",
    ) -> Any:
        """Return the catalog of the connection the test selected."""
        names = _CatalogClient.catalogs[_CatalogClient.connection]
        return mcp_types.ListToolsResult(
            tools=[
                mcp_types.Tool(
                    name=name,
                    inputSchema={"type": "object", "properties": {}},
                )
                for name in names
            ],
        )


class MCPClientToolCatalogTest(IsolatedAsyncioTestCase):
    """A cached catalog must not outlive the connection that produced it."""

    def _patch_client(self) -> Any:
        """Patch the transport and SDK client factories."""
        return (
            patch(
                "agentscope.mcp._mcp_client.stdio_client",
                side_effect=lambda _parameters: _OneShotTransport(),
            ),
            patch(
                "agentscope.mcp._mcp_client.SDKClient",
                _CatalogClient,
            ),
        )

    async def test_reconnect_rediscovers_the_catalog(self) -> None:
        """get_tool() must ask the server again after a reconnect."""
        _CatalogClient.catalogs = [["alpha"], ["beta"]]
        _CatalogClient.connection = 0

        transport_patch, sdk_patch = self._patch_client()
        with transport_patch, sdk_patch:
            client = MCPClient(
                name="catalog",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )

            await client.connect()
            listed = await client.list_raw_tools()
            self.assertEqual([tool.name for tool in listed], ["alpha"])

            await client.close()

            # The server restarted and now advertises a different tool set.
            _CatalogClient.connection = 1
            await client.connect()

            with self.assertRaises(ValueError):
                await client.get_tool("alpha")
            beta = await client.get_tool("beta")
            # The wrapper namespaces the tool as ``mcp__<client>__<tool>``.
            self.assertTrue(beta.name.endswith("beta"))

            await client.close()
