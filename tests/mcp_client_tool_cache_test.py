# -*- coding: utf-8 -*-
"""Tests that a reconnect rediscovers an MCP server's tool descriptors."""
from types import SimpleNamespace, TracebackType
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

import mcp.types

from agentscope.mcp import MCPClient, StdioMCPConfig


class _OneShotTransport:
    """A transport that refuses to be entered more than once."""

    def __init__(self) -> None:
        self.enter_count = 0

    async def __aenter__(self) -> tuple[object, object]:
        """Enter the transport context."""
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
        """Leave the transport context."""
        return False


def _echo_tool(name: str, argument: str) -> mcp.types.Tool:
    """Build a descriptor that requires one string argument.

    Args:
        name (`str`):
            The advertised tool name.
        argument (`str`):
            The name of the tool's only required argument.

    Returns:
        `mcp.types.Tool`:
            The tool descriptor.
    """
    return mcp.types.Tool(
        name=name,
        description="A test tool.",
        inputSchema={
            "type": "object",
            "properties": {argument: {"type": "string"}},
            "required": [argument],
        },
    )


class _FakeServer:
    """The tool list a fake MCP server currently advertises."""

    def __init__(self) -> None:
        self.tools: list[mcp.types.Tool] = []


class _VersionedSession:
    """A ``ClientSession`` stand-in bound to a fake server."""

    server = _FakeServer()
    """The tool list reported by the fake server."""

    list_tools_calls = 0
    """How many times the session was asked for its tool list."""

    def __init__(self, read_stream: object, write_stream: object) -> None:
        self.read_stream = read_stream
        self.write_stream = write_stream

    async def __aenter__(self) -> "_VersionedSession":
        """Enter the fake session context."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Leave the fake session context."""
        return False

    async def initialize(self) -> None:
        """Initialize the fake session."""
        return None

    async def list_tools(self) -> Any:
        """Report the tools the fake server currently advertises."""
        _VersionedSession.list_tools_calls += 1
        return SimpleNamespace(tools=list(self.server.tools))


class MCPClientToolCacheTest(IsolatedAsyncioTestCase):
    """A reconnect must not resolve the previous connection's tools."""

    def setUp(self) -> None:
        """Reset the shared fake server before every test."""
        _VersionedSession.server = _FakeServer()
        _VersionedSession.list_tools_calls = 0

    def _patched(self) -> Any:
        """Patch the transport factory and the session class.

        Both patches must be active when the client is *constructed*, because
        ``model_post_init`` builds the stdio transport eagerly.

        Returns:
            `Any`:
                A patch context manager for the fake transport and session.
        """
        return patch.multiple(
            "agentscope.mcp._mcp_client",
            stdio_client=MagicMock(
                side_effect=lambda *args, **kwargs: _OneShotTransport(),
            ),
            ClientSession=_VersionedSession,
        )

    def _client(self, name: str) -> MCPClient:
        """Create a stateful stdio client for the fake server.

        Args:
            name (`str`):
                The client name.

        Returns:
            `MCPClient`:
                A disconnected stateful client.
        """
        return MCPClient(
            name=name,
            is_stateful=True,
            mcp_config=StdioMCPConfig(command="unused"),
        )

    async def test_reconnect_rediscovers_changed_schemas(self) -> None:
        """A reconnect must expose the new schemas and new tools."""
        _VersionedSession.server.tools = [_echo_tool("echo", "text")]

        with self._patched():
            client = self._client("schema_change")
            await client.connect()
            first = await client.get_tool("echo")
            self.assertEqual(first.input_schema["required"], ["text"])
            await client.close()

            # The server renames the argument and gains a tool.
            _VersionedSession.server.tools = [
                _echo_tool("echo", "message"),
                _echo_tool("added", "value"),
            ]
            await client.connect()
            second = await client.get_tool("echo")
            self.assertEqual(second.input_schema["required"], ["message"])
            added = await client.get_tool("added")
            self.assertEqual(added.input_schema["required"], ["value"])
            await client.close()

    async def test_reconnect_drops_removed_tools(self) -> None:
        """A tool the new connection omits must no longer resolve."""
        _VersionedSession.server.tools = [
            _echo_tool("echo", "text"),
            _echo_tool("legacy", "text"),
        ]

        with self._patched():
            client = self._client("removed_tool")
            await client.connect()
            await client.get_tool("legacy")
            await client.close()

            _VersionedSession.server.tools = [_echo_tool("echo", "text")]
            await client.connect()
            with self.assertRaisesRegex(ValueError, "legacy"):
                await client.get_tool("legacy")
            await client.close()

    async def test_descriptors_stay_cached_in_one_connection(self) -> None:
        """Repeated lookups within one connection reuse the cache."""
        _VersionedSession.server.tools = [
            _echo_tool("echo", "text"),
            _echo_tool("added", "value"),
        ]

        with self._patched():
            client = self._client("single_connection")
            await client.connect()
            await client.get_tool("echo")
            await client.get_tool("added")
            self.assertEqual(_VersionedSession.list_tools_calls, 1)
            await client.close()
