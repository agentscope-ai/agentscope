# -*- coding: utf-8 -*-
"""Scheduler startup must fail, not hang, on an early listener failure."""

# pylint: disable=protected-access,unnecessary-dunder-call

import asyncio
from typing import Any, AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from agentscope.app._manager import SchedulerManager


def _fail_subscription() -> None:
    """Raise the startup failure the bus wrapper should surface."""
    raise ConnectionError("subscription failed before on_ready")


class _FailingBus:
    """A bus whose lifecycle subscription fails before readiness."""

    async def subscribe(
        self,
        *args: object,
        **kwargs: object,
    ) -> AsyncGenerator[dict, None]:
        """Raise before invoking ``on_ready``."""
        del args, kwargs
        _fail_subscription()
        yield {}


class _EndingBus:
    """A bus whose lifecycle subscription ends without readiness."""

    async def subscribe(
        self,
        *args: object,
        **kwargs: object,
    ) -> AsyncGenerator[dict, None]:
        """Return without invoking ``on_ready``."""
        del args, kwargs
        for _ in ():  # pragma: no cover - keeps this an async generator
            yield {}


class _HangingBus:
    """A bus whose lifecycle subscription blocks before readiness."""

    def __init__(self) -> None:
        """Create the signal used to observe subscription entry."""
        self.entered = asyncio.Event()

    async def subscribe(
        self,
        *args: object,
        **kwargs: object,
    ) -> AsyncGenerator[dict, None]:
        """Block until the test cancels startup."""
        del args, kwargs
        self.entered.set()
        await asyncio.Event().wait()
        for _ in ():  # pragma: no cover - unreachable
            yield {}


class _ReadyBus:
    """A bus that establishes readiness and then waits for shutdown."""

    def __init__(self) -> None:
        """Create the signal that keeps the subscription open."""
        self.release = asyncio.Event()

    async def subscribe(
        self,
        *args: object,
        **kwargs: object,
    ) -> AsyncGenerator[dict, None]:
        """Invoke the readiness callback and wait for cancellation."""
        del args
        callback = kwargs.get("on_ready")
        if callable(callback):
            callback()
        await self.release.wait()
        for _ in ():  # pragma: no cover - unreachable
            yield {}


class SchedulerStartupTest(IsolatedAsyncioTestCase):
    """The scheduler must never report a start it did not achieve."""

    def _manager(self, bus: Any) -> SchedulerManager:
        """Build a manager whose storage returns an empty schedule set."""
        storage = Mock()
        storage.list_all_schedules = AsyncMock(return_value=[])
        manager = SchedulerManager(
            storage=storage,
            message_bus=bus,
            workspace_manager=Mock(),
        )
        self.addAsyncCleanup(self._cleanup_manager, manager)
        return manager

    @staticmethod
    async def _cleanup_manager(manager: SchedulerManager) -> None:
        """Cancel a startup that a failing test left running."""
        tasks = list(manager._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        manager._tasks.clear()
        if manager._scheduler.running:
            manager._scheduler.shutdown()

    async def test_subscription_failure_before_ready_fails_startup(
        self,
    ) -> None:
        """A pre-readiness connection error propagates and cleans up."""
        manager = self._manager(_FailingBus())

        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(manager.__aenter__(), timeout=1.0)

        self.assertEqual(manager._tasks, [])
        self.assertFalse(manager._scheduler.running)

    async def test_subscription_ending_before_ready_fails_startup(
        self,
    ) -> None:
        """A subscription that ends without readiness also fails startup."""
        manager = self._manager(_EndingBus())

        with self.assertRaises(RuntimeError):
            await asyncio.wait_for(manager.__aenter__(), timeout=1.0)

        self.assertEqual(manager._tasks, [])
        self.assertFalse(manager._scheduler.running)

    async def test_cancelled_startup_cleans_up(self) -> None:
        """Cancelling startup cancels its tasks and shuts the scheduler."""
        bus = _HangingBus()
        manager = self._manager(bus)
        task = asyncio.create_task(manager.__aenter__())

        await asyncio.wait_for(bus.entered.wait(), timeout=1.0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(manager._tasks, [])
        self.assertFalse(manager._scheduler.running)

    async def test_ready_subscription_starts_and_stops(self) -> None:
        """The normal readiness path still enters and exits cleanly."""
        manager = self._manager(_ReadyBus())

        await asyncio.wait_for(manager.__aenter__(), timeout=1.0)
        self.assertTrue(manager._scheduler.running)
        await manager.__aexit__(None, None, None)

        self.assertTrue(all(task.done() for task in manager._tasks))
