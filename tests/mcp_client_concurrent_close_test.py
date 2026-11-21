# -*- coding: utf-8 -*-
"""Concurrent closing waiters must retain ownership of their connection."""
# pylint: disable=protected-access
import asyncio
import sys
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch

import mcp.types

from agentscope.mcp import HttpMCPConfig, MCPClient, StdioMCPConfig


class _GatedClient:
    """SDK context whose handshake and teardown advance only on events."""

    def __init__(self, name: str, error: bool = False) -> None:
        self.name = name
        self.error = error
        self.entered = asyncio.Event()
        self.allow_enter = asyncio.Event()
        self.exiting = asyncio.Event()
        self.allow_exit = asyncio.Event()
        self.exit_count = 0

    async def __aenter__(self) -> "_GatedClient":
        self.entered.set()
        await self.allow_enter.wait()
        return self

    async def __aexit__(self, *_args: Any) -> None:
        self.exit_count += 1
        self.exiting.set()
        await self.allow_exit.wait()
        if self.error:
            raise RuntimeError("old transport cleanup failed")

    async def list_tools(self, **_kwargs: Any) -> mcp.types.ListToolsResult:
        """Expose a distinct catalog for each connection."""
        return mcp.types.ListToolsResult(
            tools=[
                mcp.types.Tool(
                    name=self.name,
                    inputSchema={"type": "object", "properties": {}},
                ),
            ],
        )

    async def call_tool(
        self,
        name: str,
        **_kwargs: Any,
    ) -> mcp.types.CallToolResult:
        """Confirm that the newly obtained tool reaches the new session."""
        assert name == self.name
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text=self.name)],
        )


class MCPClientConcurrentCloseTest(IsolatedAsyncioTestCase):
    """Old close waiters must not erase the replacement connection."""

    def setUp(self) -> None:
        self.owners: list[asyncio.Task[Any]] = []
        self.callers: list[asyncio.Task[Any]] = []
        self.gates: list[asyncio.Event] = []
        lifecycle = MCPClient._run_client_lifecycle

        async def record_owner(*args: Any) -> None:
            """Retain cleanup ownership even when the regression loses it."""
            task = asyncio.current_task()
            assert task is not None
            self.owners.append(task)
            await lifecycle(*args)

        self.enterContext(
            patch.object(
                MCPClient,
                "_run_client_lifecycle",
                staticmethod(record_owner),
            ),
        )
        self.addAsyncCleanup(self._cleanup)

    async def _cleanup(self) -> None:
        """Also close orphan owners when a regression assertion fails."""
        for gate in self.gates:
            gate.set()
        for task in self.callers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.callers, return_exceptions=True)
        for task in self.owners:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.owners, return_exceptions=True)

    def _start(self, coroutine: Any) -> asyncio.Task[Any]:
        """Track caller tasks independently from SDK context owners."""
        task = asyncio.create_task(coroutine)
        self.callers.append(task)
        return task

    async def _exercise_reconnect(
        self,
        *,
        http: bool = False,
        policies: tuple[bool, ...] = (True, True),
        error: bool = False,
        cancel_waiter: bool = False,
    ) -> None:
        """Hold the new handshake until every old waiter has finished."""
        old = _GatedClient("old", error=error)
        new = _GatedClient("new")
        old.allow_enter.set()
        new.allow_exit.set()
        self.gates.extend(
            [old.allow_exit, new.allow_enter, new.allow_exit],
        )
        client = MCPClient(
            name="concurrent_close",
            is_stateful=True,
            mcp_config=(
                HttpMCPConfig(url="http://unused/mcp")
                if http
                else StdioMCPConfig(command="unused")
            ),
        )
        with patch.object(
            MCPClient,
            "_create_sdk_client",
            side_effect=[old, new],
        ):
            await client.connect()
            await client.get_tool("old")
            primary_errors: list[BaseException] = []

            async def close_then_reconnect() -> None:
                """The first waiter reconnects before the others unwind."""
                try:
                    await client.close(ignore_errors=policies[0])
                except (RuntimeError, asyncio.CancelledError) as exc:
                    primary_errors.append(exc)
                await client.connect()

            reconnect = self._start(close_then_reconnect())
            await asyncio.wait_for(old.exiting.wait(), 10)
            waiters = []
            for policy in policies[1:]:
                started = asyncio.Event()

                async def close_waiter(
                    ignore_errors: bool = policy,
                    entered: asyncio.Event = started,
                ) -> None:
                    """Signal entry before suspending on the old owner."""
                    entered.set()
                    await client.close(ignore_errors=ignore_errors)

                waiters.append(self._start(close_waiter()))
                await asyncio.wait_for(started.wait(), 10)

            if cancel_waiter:
                waiters[-1].cancel()
            else:
                old.allow_exit.set()
            await asyncio.wait_for(new.entered.wait(), 10)
            outcomes = await asyncio.gather(*waiters, return_exceptions=True)
            self.assertFalse(client.is_connected)
            self.assertIs(client._lifecycle_task, self.owners[-1])
            self.assertIsNotNone(client._close_requested)
            self.assertFalse(client._close_requested.is_set())
            self.assertEqual(old.exit_count, 1)
            self.assertIsNone(client._cached_tools)

            expected_error = (
                asyncio.CancelledError if cancel_waiter else RuntimeError
            )
            for policy, outcome in zip(policies[1:], outcomes):
                if cancel_waiter or (error and not policy):
                    self.assertIsInstance(outcome, expected_error)
                else:
                    self.assertIsNone(outcome)
            if cancel_waiter or (error and not policies[0]):
                self.assertEqual(len(primary_errors), 1)
                self.assertIsInstance(primary_errors[0], expected_error)
            else:
                self.assertEqual(primary_errors, [])

            new.allow_enter.set()
            await reconnect
            self.assertTrue(client.is_connected)
            with self.assertRaises(ValueError):
                await client.get_tool("old")
            tool = await client.get_tool("new")
            result = await tool()
            self.assertEqual(result.content[0].text, "new")
            await client.close(ignore_errors=False)
            self.assertFalse(client.is_connected)
            self.assertIsNone(client._lifecycle_task)
            self.assertIsNone(client._close_requested)
            self.assertEqual((old.exit_count, new.exit_count), (1, 1))

    async def test_reconnect_retains_its_owner_for_both_transports(
        self,
    ) -> None:
        """A late old closer cannot erase a STDIO or HTTP handshake."""
        for http in (False, True):
            with self.subTest(http=http):
                await self._exercise_reconnect(http=http)

    async def test_multiple_old_waiters_cannot_clear_replacement(self) -> None:
        """All waiting closes finish against the same old connection."""
        await self._exercise_reconnect(policies=(True, True, True, True))

    async def test_ignored_cleanup_error_preserves_replacement(self) -> None:
        """The default cleanup policy still permits a clean reconnect."""
        await self._exercise_reconnect(error=True)

    async def test_strict_cleanup_error_preserves_replacement(self) -> None:
        """Both callers receive the error without clearing the new owner."""
        await self._exercise_reconnect(error=True, policies=(False, False))

    async def test_each_closer_retains_its_error_policy(self) -> None:
        """Ignoring cleanup errors remains a per-caller decision."""
        for policies in ((True, False), (False, True)):
            with self.subTest(policies=policies):
                await self._exercise_reconnect(error=True, policies=policies)

    async def test_cancelled_waiter_does_not_erase_reconnect(self) -> None:
        """Shared lifecycle cancellation still propagates to both callers."""
        await self._exercise_reconnect(cancel_waiter=True)

    async def test_concurrent_close_without_reconnect(self) -> None:
        """Concurrent callers close one SDK context and clear its cache."""
        old = _GatedClient("old")
        old.allow_enter.set()
        self.gates.append(old.allow_exit)
        client = MCPClient(
            name="close_only",
            is_stateful=True,
            mcp_config=StdioMCPConfig(command="unused"),
        )
        with patch.object(MCPClient, "_create_sdk_client", return_value=old):
            await client.connect()
            await client.get_tool("old")
            first = self._start(client.close())
            await asyncio.wait_for(old.exiting.wait(), 10)
            entered = asyncio.Event()

            async def second_close() -> None:
                entered.set()
                await client.close()

            second = self._start(second_close())
            await asyncio.wait_for(entered.wait(), 10)
            old.allow_exit.set()
            await asyncio.gather(first, second)
            self.assertEqual(old.exit_count, 1)
            self.assertFalse(client.is_connected)
            self.assertIsNone(client._client)
            self.assertIsNone(client._lifecycle_task)
            self.assertIsNone(client._close_requested)
            self.assertIsNone(client._cached_tools)
            with self.assertRaisesRegex(RuntimeError, "is not connected"):
                await client.close()

    async def test_real_stdio_reconnect_remains_callable_and_closable(
        self,
    ) -> None:
        """Real STDIO sessions remain usable after overlapping closes."""
        server = '''from mcp.server import MCPServer
import os
server = MCPServer("close-owner-test")
@server.tool()
def process_id() -> str:
    """Return this MCP server's process ID."""
    return str(os.getpid())
server.run(transport="stdio")
'''
        client = MCPClient(
            name="real_close",
            is_stateful=True,
            mcp_config=StdioMCPConfig(
                command=sys.executable,
                args=["-u", "-c", server],
            ),
        )
        async with asyncio.timeout(60):
            await client.connect()
            old_tool = await client.get_tool("process_id")
            old_pid = (await old_tool()).content[0].text

            async def reconnect() -> None:
                await client.close()
                await client.connect()

            await asyncio.gather(
                self._start(reconnect()),
                self._start(client.close()),
            )
            new_tool = await client.get_tool("process_id")
            new_pid = (await new_tool()).content[0].text
            self.assertNotEqual(old_pid, new_pid)
            self.assertTrue(client.is_connected)
            await client.close(ignore_errors=False)
            self.assertFalse(client.is_connected)
            self.assertTrue(all(task.done() for task in self.owners))
