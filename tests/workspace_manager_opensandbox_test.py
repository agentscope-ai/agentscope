# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Shared sandbox lifecycle regressions; no provider SDK required."""

import asyncio
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.app.workspace_manager import (
    LocalWorkspaceLifecycle,
    OpenSandboxWorkspaceManager,
    RedisWorkspaceLifecycle,
)


class _Workspace:
    """Separate local handles to a shared remote sandbox."""

    paused = False
    pauses = 0
    pause_started = None
    pause_continue = None

    def __init__(self, **kwargs: object) -> None:
        self.workspace_id = kwargs["workspace_id"]
        self.detached = False

    async def initialize(self) -> None:
        """Connect or resume the shared sandbox."""
        _Workspace.paused = False

    async def release_local(self) -> None:
        """Detach without changing the remote state."""
        self.detached = True

    async def pause_remote(self) -> None:
        """Model a controllable pause RPC."""
        if _Workspace.pause_started is not None:
            _Workspace.pause_started.set()
            await _Workspace.pause_continue.wait()
        _Workspace.paused = True
        _Workspace.pauses += 1


class TestOpenSandboxWorkspaceManager(IsolatedAsyncioTestCase):
    """Protect long usage, other nodes, and collector transitions."""

    async def asyncSetUp(self) -> None:
        """Install the shared sandbox double."""
        _Workspace.paused = False
        _Workspace.pauses = 0
        _Workspace.pause_started = None
        _Workspace.pause_continue = None
        self.patch = patch(
            "agentscope.app.workspace_manager."
            "_opensandbox_workspace_manager.OpenSandboxWorkspace",
            _Workspace,
        )
        self.patch.start()
        self.lifecycle = LocalWorkspaceLifecycle()

    async def asyncTearDown(self) -> None:
        """Remove provider patch."""
        self.patch.stop()

    def manager(self, **kwargs: object) -> OpenSandboxWorkspaceManager:
        """Build a manager in this test's shared namespace."""
        return OpenSandboxWorkspaceManager(
            lifecycle=self.lifecycle,
            ttl=0,
            usage_lease_seconds=0.15,
            **kwargs,
        )

    async def test_long_turn_renews_and_pins_local_handle(self) -> None:
        """An active turn survives several lease periods and TTL ticks."""
        manager = self.manager()
        async with manager.acquire_workspace("u", "a", "s", "x") as ws:
            await asyncio.sleep(0.4)
            await manager._sweep_once()
            await manager.close_all()
            self.assertFalse(ws.detached)
            self.assertFalse(_Workspace.paused)
        await manager._sweep_once()
        self.assertTrue(ws.detached)
        self.assertTrue(_Workspace.paused)

    async def test_other_node_expiry_and_shutdown_do_not_pause(self) -> None:
        """A's eviction and shutdown leave B's active sandbox usable."""
        first, second = self.manager(), self.manager()
        async with first.acquire_workspace("u", "a", "s", "x") as a:
            pass
        async with second.acquire_workspace("u", "a", "s", "x") as b:
            await first._sweep_once()
            self.assertTrue(a.detached)
            await first.__aexit__()
            self.assertFalse(b.detached)
            self.assertFalse(_Workspace.paused)
        await second._sweep_once()
        self.assertTrue(_Workspace.paused)

    async def test_pause_excludes_new_user_and_invalidates_cache(self) -> None:
        """A new user waits for pause then reconnects a stale local handle."""
        first, second = self.manager(), self.manager()
        async with second.acquire_workspace("u", "a", "s", "x") as stale:
            pass
        _Workspace.pause_started = asyncio.Event()
        _Workspace.pause_continue = asyncio.Event()
        collecting = asyncio.create_task(first._sweep_once())
        await _Workspace.pause_started.wait()
        entered = asyncio.Event()

        async def borrow() -> None:
            async with second.acquire_workspace("u", "a", "s", "x") as ws:
                self.assertIsNot(ws, stale)
                self.assertTrue(stale.detached)
                self.assertFalse(_Workspace.paused)
                entered.set()

        borrowing = asyncio.create_task(borrow())
        await asyncio.sleep(0.02)
        self.assertFalse(entered.is_set())
        _Workspace.pause_continue.set()
        await asyncio.wait_for(asyncio.gather(collecting, borrowing), 2)
        await second.close_all()

    async def test_task_scoped_legacy_get(self) -> None:
        """Existing file/realtime callers retain protection until task end."""
        manager = self.manager()
        started, finish = asyncio.Event(), asyncio.Event()

        async def request() -> None:
            ws = await manager.get_workspace("u", "a", "s", "x")
            self.assertIs(
                ws,
                await manager.get_workspace("u", "a", "s", "x"),
            )
            started.set()
            await finish.wait()

        request_task = asyncio.create_task(request())
        await started.wait()
        await manager._sweep_once()
        self.assertFalse(_Workspace.paused)
        finish.set()
        await request_task
        await asyncio.wait_for(manager._drained.wait(), 1)
        await manager._sweep_once()
        self.assertTrue(_Workspace.paused)

    async def test_shutdown_drains_active_local_user(self) -> None:
        """Shutdown waits for this node's users and never pauses remote."""
        manager = self.manager()
        async with manager.acquire_workspace("u", "a", "s", "x") as ws:
            shutdown = asyncio.create_task(manager.__aexit__())
            await asyncio.sleep(0)
            self.assertFalse(shutdown.done())
            self.assertFalse(ws.detached)
        await asyncio.wait_for(shutdown, 1)
        self.assertTrue(ws.detached)
        self.assertFalse(_Workspace.paused)
        with self.assertRaisesRegex(RuntimeError, "closing"):
            async with manager.acquire_workspace("u", "a", "s", "x"):
                self.fail("Admission after shutdown")

    async def test_shutdown_releases_callers_legacy_borrow(self) -> None:
        """Using get_workspace inside manager context does not deadlock."""
        manager = self.manager()
        async with manager:
            ws = await manager.get_workspace("u", "a", "s", "x")
        self.assertTrue(ws.detached)
        self.assertFalse(_Workspace.paused)
        self.assertFalse(manager._borrowers)

    async def test_cancelled_execution_releases_usage(self) -> None:
        """Cancellation drains local pins and global usage leases."""
        manager = self.manager()
        entered = asyncio.Event()

        async def execute() -> None:
            async with manager.acquire_workspace("u", "a", "s", "x"):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(execute())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(manager._active)
        await manager._sweep_once()
        self.assertTrue(_Workspace.paused)

    async def test_renewal_failure_cancels_execution(self) -> None:
        """Lost protection ends execution instead of silently continuing."""
        manager = self.manager()
        original = self.lifecycle.update

        async def update(
            wid: str,
            operation: str,
            token: str,
            duration: float,
        ) -> int:
            if operation == "renew":
                return 0
            return await original(wid, operation, token, duration)

        with patch.object(self.lifecycle, "update", side_effect=update):
            with self.assertRaisesRegex(RuntimeError, "lease was lost"):
                async with manager.acquire_workspace("u", "a", "s", "x"):
                    await asyncio.sleep(1)
        self.assertFalse(manager._active)

    async def test_graceful_exit_settles_inflight_pause(self) -> None:
        """Stopping a sweeper waits for its remote pause to settle."""
        manager = self.manager()
        async with manager.acquire_workspace("u", "a", "s", "x"):
            pass
        _Workspace.pause_started = asyncio.Event()
        _Workspace.pause_continue = asyncio.Event()
        manager._sweep_task = asyncio.create_task(manager._sweep_once())
        await _Workspace.pause_started.wait()
        shutdown = asyncio.create_task(manager.__aexit__())
        await asyncio.sleep(0)
        self.assertFalse(shutdown.done())
        _Workspace.pause_continue.set()
        await asyncio.wait_for(shutdown, 1)
        self.assertGreater(
            await self.lifecycle.update("x", "acquire", "new", 10),
            0,
        )

    async def test_ambiguous_pause_retains_claim(self) -> None:
        """A delayed or failed pause cannot race admitted new users."""
        manager = self.manager()
        async with manager.acquire_workspace("u", "a", "s", "x"):
            pass
        with patch.object(
            _Workspace,
            "pause_remote",
            AsyncMock(side_effect=OSError),
        ):
            await manager._sweep_once()
        self.assertEqual(
            await self.lifecycle.update("x", "acquire", "new", 10),
            0,
        )


class TestRedisWorkspaceLifecycle(IsolatedAsyncioTestCase):
    """Exercise the actual Redis Lua protocol with independent clients."""

    async def asyncSetUp(self) -> None:
        """Create two Redis clients in one namespace."""
        import fakeredis.aioredis

        server = fakeredis.FakeServer()
        self.clients = [
            fakeredis.aioredis.FakeRedis(server=server) for _ in range(2)
        ]
        self.first = RedisWorkspaceLifecycle(self.clients[0])
        self.second = RedisWorkspaceLifecycle(self.clients[1])

    async def asyncTearDown(self) -> None:
        """Close both clients."""
        for client in self.clients:
            await client.aclose()

    async def test_shared_users_and_global_idle(self) -> None:
        """A collector cannot claim until all nodes release their leases."""
        self.assertEqual(await self.first.update("x", "acquire", "a", 10), 1)
        self.assertEqual(await self.second.update("x", "acquire", "b", 10), 1)
        await self.first.update("x", "release", "a", 0)
        self.assertEqual(await self.first.update("x", "claim", "c", 0), 0)
        await self.second.update("x", "release", "b", 0)
        self.assertEqual(await self.first.update("x", "claim", "c", 10), 0)
        self.assertEqual(await self.first.update("x", "claim", "c", 0), 1)
        self.assertEqual(await self.second.update("x", "acquire", "b", 10), 0)
        self.assertEqual(
            await self.second.update("x", "finish", "stale", 0),
            0,
        )
        self.assertEqual(await self.first.update("x", "finish", "c", 0), 1)
        self.assertEqual(await self.second.update("x", "acquire", "b", 10), 2)
        self.assertEqual(await self.second.candidates(), ["x"])

    async def test_crashed_user_expires_and_cannot_renew(self) -> None:
        """An expired borrower cannot resurrect a lease after collection."""
        await self.first.update("x", "acquire", "crashed", 0.01)
        await asyncio.sleep(0.03)
        self.assertEqual(
            await self.first.update("x", "renew", "crashed", 10),
            0,
        )
        self.assertEqual(await self.second.update("x", "claim", "c", 0), 1)
        self.assertEqual(
            await self.first.update("x", "renew", "crashed", 10),
            0,
        )


class TestOpenSandboxLocalRelease(IsolatedAsyncioTestCase):
    """Validate real workspace local cleanup without a sandbox server."""

    async def test_local_release_closes_clients_without_pause(self) -> None:
        """Local release leaves the remote sandbox and gateway running."""
        from agentscope.workspace import OpenSandboxWorkspace

        workspace = OpenSandboxWorkspace(workspace_id="x")
        sandbox = AsyncMock()
        gateway = AsyncMock()
        workspace._sandbox = sandbox
        workspace._gateway = gateway
        workspace.is_alive = True
        await workspace.release_local()
        await workspace.release_local()
        sandbox.close.assert_awaited_once()
        sandbox.pause.assert_not_awaited()
        gateway.aclose.assert_awaited_once()
        self.assertIsNone(workspace._backend)
        self.assertFalse(workspace.is_alive)

    async def test_gateway_failure_still_releases_sandbox_client(self) -> None:
        """One cleanup failure does not prevent remaining local cleanup."""
        from agentscope.workspace import OpenSandboxWorkspace

        workspace = OpenSandboxWorkspace(workspace_id="x")
        sandbox = AsyncMock()
        gateway = AsyncMock()
        gateway.aclose.side_effect = OSError("local client error")
        workspace._sandbox = sandbox
        workspace._gateway = gateway
        with self.assertRaises(OSError):
            await workspace.release_local()
        sandbox.close.assert_awaited_once()
        sandbox.pause.assert_not_awaited()
        self.assertIsNone(workspace._sandbox)


if __name__ == "__main__":
    unittest.main()
