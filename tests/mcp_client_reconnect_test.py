# -*- coding: utf-8 -*-
"""Tests for reconnecting stateful MCP clients."""
import asyncio
from types import TracebackType
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

import anyio
from mcp.types import ListToolsResult, Tool

from agentscope.mcp import HttpMCPConfig, MCPClient, StdioMCPConfig


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


class _FakeSession:
    """Small ClientSession stand-in for lifecycle-only tests."""

    def __init__(self, read_stream: object, write_stream: object) -> None:
        self.read_stream = read_stream
        self.write_stream = write_stream
        self.exit_count = 0

    async def __aenter__(self) -> "_FakeSession":
        """Enter the fake session context."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Leave the fake session context."""
        self.exit_count += 1
        return False

    async def initialize(self) -> None:
        """Initialize the fake session."""
        return None


class _ToolListingSession(_FakeSession):
    """Expose a connection-specific catalog and count discovery requests."""

    def __init__(
        self,
        read_stream: object,
        write_stream: object,
        tools: list[Tool],
        fail_initialize: bool = False,
    ) -> None:
        super().__init__(read_stream, write_stream)
        self.tools = tools
        self.fail_initialize = fail_initialize
        self.list_tools_calls = 0

    async def initialize(self) -> None:
        """Optionally fail this connection's initialization."""
        if self.fail_initialize:
            raise RuntimeError("initialization failed")

    async def list_tools(self) -> ListToolsResult:
        """Return this session's catalog."""
        self.list_tools_calls += 1
        return ListToolsResult(tools=self.tools)


class MCPClientReconnectTest(IsolatedAsyncioTestCase):
    """Stateful MCP transports must be recreated for every connection."""

    async def test_stdio_client_can_reconnect_after_close(self) -> None:
        """A stdio client must get a new transport after close()."""
        transports: list[_OneShotTransport] = []

        def create_transport(_parameters: Any) -> _OneShotTransport:
            """Create and retain a one-shot transport for assertions."""
            transport = _OneShotTransport()
            transports.append(transport)
            return transport

        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            side_effect=create_transport,
        ), patch(
            "agentscope.mcp._mcp_client.ClientSession",
            _FakeSession,
        ):
            client = MCPClient(
                name="reconnect_stdio",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )

            await client.connect()
            await client.close()
            await client.connect()
            await client.close()

        self.assertEqual(len(transports), 2)
        self.assertTrue(all(_.enter_count == 1 for _ in transports))

    async def test_failed_connect_can_be_retried(self) -> None:
        """A failed connection must discard its one-shot transport."""
        transports: list[_OneShotTransport] = []

        def create_transport(_parameters: Any) -> _OneShotTransport:
            """Create and retain a one-shot transport for assertions."""
            transport = _OneShotTransport()
            transports.append(transport)
            return transport

        class _FailOnceSession(_FakeSession):
            """Fail the first initialization, then allow a retry."""

            attempts = 0

            async def initialize(self) -> None:
                _FailOnceSession.attempts += 1
                if _FailOnceSession.attempts == 1:
                    raise RuntimeError("initialization failed")

        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            side_effect=create_transport,
        ), patch(
            "agentscope.mcp._mcp_client.ClientSession",
            _FailOnceSession,
        ):
            client = MCPClient(
                name="retry_after_failed_connect",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )

            with self.assertRaisesRegex(RuntimeError, "initialization failed"):
                await client.connect()
            await client.connect()
            await client.close()

        self.assertEqual(len(transports), 2)
        self.assertTrue(all(_.enter_count == 1 for _ in transports))

    async def test_cancelled_connect_closes_partial_connection(self) -> None:
        """Cancellation during initialization must clean up and allow retry."""
        initialize_started = asyncio.Event()
        never_finish = asyncio.Event()
        transports: list[_OneShotTransport] = []

        def create_transport(_parameters: Any) -> _OneShotTransport:
            """Create and retain one-shot transports for assertions."""
            transport = _OneShotTransport()
            transports.append(transport)
            return transport

        class _BlockingSession(_FakeSession):
            """Block initialization until the test cancels the connection."""

            instance: "_BlockingSession | None" = None
            attempts = 0

            def __init__(
                self,
                read_stream: object,
                write_stream: object,
            ) -> None:
                super().__init__(read_stream, write_stream)
                _BlockingSession.instance = self

            async def initialize(self) -> None:
                _BlockingSession.attempts += 1
                initialize_started.set()
                if _BlockingSession.attempts == 1:
                    await never_finish.wait()

        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            side_effect=create_transport,
        ), patch(
            "agentscope.mcp._mcp_client.ClientSession",
            _BlockingSession,
        ):
            client = MCPClient(
                name="cancelled_connect",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )

            task = asyncio.create_task(client.connect())
            await initialize_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

            session = _BlockingSession.instance
            assert session is not None
            self.assertFalse(client.is_connected)
            self.assertEqual(transports[0].exit_count, 1)
            self.assertEqual(session.exit_count, 1)

            await client.connect()
            await client.close()

        self.assertEqual(len(transports), 2)
        self.assertTrue(all(_.enter_count == 1 for _ in transports))
        self.assertTrue(all(_.exit_count == 1 for _ in transports))

    async def test_cancel_scope_does_not_abandon_the_transport(self) -> None:
        """A cancel scope must not cut the cleanup short."""
        initialize_started = asyncio.Event()
        transport_closed = asyncio.Event()

        class _SlowTransport(_OneShotTransport):
            """Await while closing, the way a real stdio transport does."""

            async def __aexit__(
                self,
                exc_type: type[BaseException] | None,
                exc: BaseException | None,
                traceback: TracebackType | None,
            ) -> bool:
                await asyncio.sleep(0)
                await super().__aexit__(exc_type, exc, traceback)
                transport_closed.set()
                return False

        class _BlockingSession(_FakeSession):
            """Never finish initializing, so the scope cancels mid-connect."""

            async def initialize(self) -> None:
                initialize_started.set()
                await asyncio.Event().wait()

        transport = _SlowTransport()
        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            return_value=transport,
        ), patch(
            "agentscope.mcp._mcp_client.ClientSession",
            _BlockingSession,
        ):
            client = MCPClient(
                name="cancel_scope",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )

            with anyio.CancelScope() as scope:

                async def _cancel_once_started() -> None:
                    """Cancel the scope once the connect is blocked."""
                    await initialize_started.wait()
                    scope.cancel()

                canceller = asyncio.create_task(_cancel_once_started())
                await client.connect()
            await canceller

        self.assertTrue(scope.cancelled_caught)
        # Cleanup runs to completion even though the scope keeps cancelling.
        await asyncio.wait_for(transport_closed.wait(), 1)
        self.assertEqual(transport.exit_count, 1)
        self.assertFalse(client.is_connected)

    async def test_http_client_can_reconnect_after_close(self) -> None:
        """An HTTP client must get a new transport after close()."""
        transports: list[_OneShotTransport] = []

        def create_transport() -> _OneShotTransport:
            """Create and retain a one-shot transport for assertions."""
            transport = _OneShotTransport()
            transports.append(transport)
            return transport

        with patch.object(
            MCPClient,
            "_create_http_client",
            side_effect=create_transport,
        ), patch(
            "agentscope.mcp._mcp_client.ClientSession",
            _FakeSession,
        ):
            client = MCPClient(
                name="reconnect_http",
                is_stateful=True,
                mcp_config=HttpMCPConfig(url="http://unused"),
            )

            await client.connect()
            await client.close()
            await client.connect()
            await client.close()

        self.assertEqual(len(transports), 2)
        self.assertTrue(all(_.enter_count == 1 for _ in transports))


class MCPClientToolCacheReconnectTest(IsolatedAsyncioTestCase):
    """Tool discovery is lazy and cached only within one connection."""

    # Session identity is part of the reconnect assertions below.
    # pylint: disable=protected-access

    def setUp(self) -> None:
        """Give each connection its own session and tool catalog."""
        self.catalogs = [
            [
                Tool(
                    name="echo",
                    inputSchema={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                ),
                Tool(name="removed_tool", inputSchema={"type": "object"}),
            ],
            [
                Tool(
                    name="echo",
                    inputSchema={
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    },
                ),
                Tool(name="added_tool", inputSchema={"type": "object"}),
            ],
        ]
        self.sessions: list[_ToolListingSession] = []
        self.failed_connections: set[int] = set()

        def create_session(
            read_stream: object,
            write_stream: object,
        ) -> _ToolListingSession:
            """Select the catalog and initialization outcome by connection."""
            connection = len(self.sessions)
            session = _ToolListingSession(
                read_stream,
                write_stream,
                tools=self.catalogs[connection],
                fail_initialize=connection in self.failed_connections,
            )
            self.sessions.append(session)
            return session

        transport_patch = patch(
            "agentscope.mcp._mcp_client.stdio_client",
            side_effect=lambda _parameters: _OneShotTransport(),
        )
        transport_patch.start()
        self.addCleanup(transport_patch.stop)
        session_patch = patch(
            "agentscope.mcp._mcp_client.ClientSession",
            side_effect=create_session,
        )
        session_patch.start()
        self.addCleanup(session_patch.stop)
        self.client = MCPClient(
            name="reconnect_tool_cache",
            is_stateful=True,
            mcp_config=StdioMCPConfig(command="unused"),
        )

    async def asyncTearDown(self) -> None:
        """Close the live connection even when an assertion fails."""
        if self.client.is_connected:
            await self.client.close(ignore_errors=False)

    async def test_reconnect_refreshes_tool_schema(self) -> None:
        """Newly obtained tools use the new schema and the new session."""
        await self.client.connect()
        self.assertEqual(self.sessions[0].list_tools_calls, 0)
        old_tool = await self.client.get_tool("echo")
        self.assertIn("text", old_tool.input_schema["properties"])
        self.assertIs(old_tool._session, self.sessions[0])

        await self.client.close()
        await self.client.connect()
        self.assertEqual(self.sessions[1].list_tools_calls, 0)
        new_tool = await self.client.get_tool("echo")
        self.assertIn("message", new_tool.input_schema["properties"])
        self.assertNotIn("text", new_tool.input_schema["properties"])
        self.assertIs(new_tool._session, self.sessions[1])
        self.assertIsNot(new_tool._session, old_tool._session)
        self.assertEqual(self.sessions[1].list_tools_calls, 1)

    async def test_reconnect_discovers_added_tool(self) -> None:
        """A tool absent from the old catalog becomes available."""
        await self.client.connect()
        with self.assertRaisesRegex(ValueError, "Tool 'added_tool' not found"):
            await self.client.get_tool("added_tool")

        await self.client.close()
        await self.client.connect()
        tool = await self.client.get_tool("added_tool")
        self.assertIs(tool._session, self.sessions[1])
        self.assertEqual(self.sessions[1].list_tools_calls, 1)

    async def test_reconnect_discards_removed_tool(self) -> None:
        """Tools removed by the server cannot be obtained after reconnect."""
        await self.client.connect()
        await self.client.get_tool("removed_tool")

        await self.client.close()
        await self.client.connect()
        with self.assertRaisesRegex(
            ValueError,
            "Tool 'removed_tool' not found",
        ):
            await self.client.get_tool("removed_tool")
        self.assertEqual(self.sessions[1].list_tools_calls, 1)

    async def test_empty_catalog_is_cached_until_reconnect(self) -> None:
        """An empty catalog is queried once and invalidated on reconnect."""
        self.catalogs.insert(1, [])
        await self.client.connect()
        await self.client.get_tool("echo")

        await self.client.close()
        await self.client.connect()
        self.assertEqual(self.sessions[1].list_tools_calls, 0)
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "Tool 'echo' not found"):
                await self.client.get_tool("echo")
        self.assertEqual(self.sessions[1].list_tools_calls, 1)

        await self.client.close()
        await self.client.connect()
        tool = await self.client.get_tool("echo")
        self.assertIn("message", tool.input_schema["properties"])
        self.assertEqual(self.sessions[2].list_tools_calls, 1)

    async def test_failed_reconnect_then_success_refreshes_catalog(
        self,
    ) -> None:
        """A failed initialization cannot prevent discovery on recovery."""
        self.catalogs.insert(1, [])
        self.failed_connections.add(1)
        await self.client.connect()
        await self.client.get_tool("echo")
        await self.client.close()

        with self.assertRaisesRegex(RuntimeError, "initialization failed"):
            await self.client.connect()
        self.assertFalse(self.client.is_connected)
        self.assertEqual(self.sessions[1].list_tools_calls, 0)
        with self.assertRaisesRegex(RuntimeError, "not connected"):
            await self.client.get_tool("echo")

        await self.client.connect()
        self.assertEqual(self.sessions[2].list_tools_calls, 0)
        tool = await self.client.get_tool("echo")
        self.assertIn("message", tool.input_schema["properties"])
        self.assertNotIn("text", tool.input_schema["properties"])
        self.assertIs(tool._session, self.sessions[2])
        self.assertEqual(self.sessions[2].list_tools_calls, 1)

    async def test_same_connection_reuses_catalog(self) -> None:
        """Repeated lookups within one connection list tools only once."""
        await self.client.connect()
        self.assertEqual(self.sessions[0].list_tools_calls, 0)
        for _ in range(2):
            tool = await self.client.get_tool("echo")
            self.assertIs(tool._session, self.sessions[0])
            self.assertIn("text", tool.input_schema["properties"])
        self.assertEqual(self.sessions[0].list_tools_calls, 1)

    async def _assert_filtered_catalogs(
        self,
        expected_names: list[list[str]],
    ) -> None:
        """Check filtering and direct lookup against both server versions."""
        for connection, names in enumerate(expected_names):
            await self.client.connect()
            self.assertEqual(self.sessions[connection].list_tools_calls, 0)
            tool = await self.client.get_tool("echo")
            parameter = "text" if connection == 0 else "message"
            self.assertIn(parameter, tool.input_schema["properties"])
            self.assertEqual(self.sessions[connection].list_tools_calls, 1)

            raw_tools = await self.client.list_raw_tools()
            self.assertEqual([tool.name for tool in raw_tools], names)
            # Filtering affects listing, but direct lookup still uses the
            # complete catalog from the current connection.
            for descriptor in self.catalogs[connection]:
                tool = await self.client.get_tool(descriptor.name)
                self.assertIs(tool._session, self.sessions[connection])
            self.assertEqual(self.sessions[connection].list_tools_calls, 2)
            await self.client.close()

    async def test_enable_tools_filter_survives_reconnect(self) -> None:
        """An allowlist filters the refreshed catalog after reconnect."""
        self.client.enable_tools = ["echo", "added_tool"]
        await self._assert_filtered_catalogs(
            [["echo"], ["echo", "added_tool"]],
        )

    async def test_disable_tools_filter_survives_reconnect(self) -> None:
        """A denylist filters old and newly added tools after reconnect."""
        self.client.disable_tools = ["removed_tool", "added_tool"]
        await self._assert_filtered_catalogs([["echo"], ["echo"]])
