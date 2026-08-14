# -*- coding: utf-8 -*-
"""Tests for reconnecting stateful MCP clients."""
from types import TracebackType
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.mcp import HttpMCPConfig, MCPClient, StdioMCPConfig


class _OneShotTransport:
    """Minimal transport context manager that cannot be entered twice."""

    def __init__(self) -> None:
        self.enter_count = 0

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
        return False


class _FakeSession:
    """Small ClientSession stand-in for lifecycle-only tests."""

    def __init__(self, read_stream: object, write_stream: object) -> None:
        self.read_stream = read_stream
        self.write_stream = write_stream

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
        return False

    async def initialize(self) -> None:
        """Initialize the fake session."""
        return None


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
