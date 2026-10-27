# -*- coding: utf-8 -*-
"""Tests for reconnecting stateful MCP clients."""
import asyncio
from types import TracebackType
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

import anyio

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


class _FakeClient:
    """Small MCP 2.x Client stand-in for lifecycle-only tests."""

    def __init__(
        self,
        transport: _OneShotTransport,
        *,
        mode: str,
    ) -> None:
        self.transport = transport
        self.mode = mode
        self.exit_count = 0
        self._entered = False

    async def __aenter__(self) -> "_FakeClient":
        """Enter the fake client and clean partial connections on failure."""
        await self.transport.__aenter__()
        self._entered = True
        try:
            await self.connect()
        except BaseException:
            await self.transport.__aexit__(None, None, None)
            self._entered = False
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Leave the fake client context."""
        self.exit_count += 1
        if self._entered:
            await self.transport.__aexit__(exc_type, exc, traceback)
            self._entered = False
        return False

    async def connect(self) -> None:
        """Hook used by failure and cancellation tests."""
        return None


class _TaskBoundClient(_FakeClient):
    """Client that requires context entry and exit in the same task."""

    instance: "_TaskBoundClient | None" = None

    def __init__(
        self,
        transport: _OneShotTransport,
        *,
        mode: str,
    ) -> None:
        super().__init__(transport, mode=mode)
        self.enter_task: asyncio.Task[Any] | None = None
        self.exit_task: asyncio.Task[Any] | None = None
        _TaskBoundClient.instance = self

    async def __aenter__(self) -> "_TaskBoundClient":
        """Record the task that owns the client context."""
        self.enter_task = asyncio.current_task()
        await super().__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Reject the AnyIO-invalid cross-task context exit."""
        self.exit_task = asyncio.current_task()
        if self.exit_task is not self.enter_task:
            raise RuntimeError(
                "Attempted to exit cancel scope in a different task",
            )
        return await super().__aexit__(exc_type, exc, traceback)


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
            "agentscope.mcp._mcp_client.SDKClient",
            _FakeClient,
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

    async def test_client_context_is_closed_by_its_owner_task(self) -> None:
        """Connect and close requests may run in different caller tasks."""
        transport = _OneShotTransport()
        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            return_value=transport,
        ), patch(
            "agentscope.mcp._mcp_client.SDKClient",
            _TaskBoundClient,
        ):
            client = MCPClient(
                name="task_bound",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="unused"),
            )

            connect_task = asyncio.create_task(client.connect())
            await connect_task
            close_task = asyncio.create_task(client.close())
            await close_task

        sdk_client = _TaskBoundClient.instance
        assert sdk_client is not None
        self.assertIs(sdk_client.enter_task, sdk_client.exit_task)
        self.assertIsNot(sdk_client.enter_task, connect_task)
        self.assertIsNot(sdk_client.exit_task, close_task)
        self.assertEqual(
            {
                "transport_enters": transport.enter_count,
                "transport_exits": transport.exit_count,
                "client_exits": sdk_client.exit_count,
            },
            {
                "transport_enters": 1,
                "transport_exits": 1,
                "client_exits": 1,
            },
        )

    async def test_failed_connect_can_be_retried(self) -> None:
        """A failed connection must discard its one-shot transport."""
        transports: list[_OneShotTransport] = []

        def create_transport(_parameters: Any) -> _OneShotTransport:
            """Create and retain a one-shot transport for assertions."""
            transport = _OneShotTransport()
            transports.append(transport)
            return transport

        class _FailOnceClient(_FakeClient):
            """Fail the first connection, then allow a retry."""

            attempts = 0

            async def connect(self) -> None:
                _FailOnceClient.attempts += 1
                if _FailOnceClient.attempts == 1:
                    raise RuntimeError("initialization failed")

        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            side_effect=create_transport,
        ), patch(
            "agentscope.mcp._mcp_client.SDKClient",
            _FailOnceClient,
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

        class _BlockingClient(_FakeClient):
            """Block connection until the test cancels it."""

            instance: "_BlockingClient | None" = None
            attempts = 0

            def __init__(
                self,
                transport: _OneShotTransport,
                *,
                mode: str,
            ) -> None:
                super().__init__(transport, mode=mode)
                _BlockingClient.instance = self

            async def connect(self) -> None:
                _BlockingClient.attempts += 1
                initialize_started.set()
                if _BlockingClient.attempts == 1:
                    await never_finish.wait()

        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            side_effect=create_transport,
        ), patch(
            "agentscope.mcp._mcp_client.SDKClient",
            _BlockingClient,
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

            sdk_client = _BlockingClient.instance
            assert sdk_client is not None
            self.assertFalse(client.is_connected)
            self.assertEqual(transports[0].exit_count, 1)
            self.assertEqual(sdk_client.exit_count, 0)

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

        class _BlockingClient(_FakeClient):
            """Never finish connecting, so the scope cancels mid-connect."""

            async def connect(self) -> None:
                initialize_started.set()
                await asyncio.Event().wait()

        transport = _SlowTransport()
        with patch(
            "agentscope.mcp._mcp_client.stdio_client",
            return_value=transport,
        ), patch(
            "agentscope.mcp._mcp_client.SDKClient",
            _BlockingClient,
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
            "agentscope.mcp._mcp_client.SDKClient",
            _FakeClient,
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
