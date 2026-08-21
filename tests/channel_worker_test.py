# -*- coding: utf-8 -*-
"""Tests for the standalone channel worker's lifecycle.

The worker is the process that owns the platforms' long connections, so
what matters here is that it opens its backends, stays up, and releases
them on the way out.

Shutdown is deliberately not driven by a real signal: the worker only
installs handlers where the event loop supports them, so on Windows a
raised SIGTERM would take the default action and kill the test process
rather than fail a test. The two halves are checked separately instead
— that the handlers are registered, and that teardown releases
everything.
"""
import asyncio
import signal
import sys
from types import TracebackType
from typing import Any
from unittest import IsolatedAsyncioTestCase, skipIf

from agentscope.app.channel.worker import run_channel_worker


class _TrackedContext:
    """Async-context backend recording when it opened and closed."""

    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> "_TrackedContext":
        """Record that the worker opened this backend."""
        self.entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Record that the worker released this backend."""
        self.exited = True


class _Storage(_TrackedContext):
    """Storage stub with no channels to run."""

    async def list_all_channels(self) -> list:
        """No records, so the worker opens no connections."""
        return []


class _Bus(_TrackedContext):
    """Bus stub whose lifecycle subscription never yields."""

    async def subscribe(  # pylint: disable=unused-argument
        self,
        key: str,
        **kwargs: Any,
    ) -> Any:
        """Block forever, as a live subscription would."""
        await asyncio.Event().wait()
        yield {}  # pragma: no cover

    async def registry_set(self, *args: Any, **kwargs: Any) -> None:
        """Accept heartbeats."""


class ChannelWorkerLifecycleTest(IsolatedAsyncioTestCase):
    """The worker holds its backends open until told to stop."""

    async def _start(
        self,
    ) -> tuple[asyncio.Task, _Storage, _Bus, _TrackedContext]:
        """Run a worker over stub backends and wait for it to come up."""
        storage, bus, workspaces = _Storage(), _Bus(), _TrackedContext()
        worker = asyncio.create_task(
            run_channel_worker(
                storage=storage,
                message_bus=bus,
                workspace_manager=workspaces,
                channels=[],
            ),
        )
        await asyncio.sleep(0.05)
        return worker, storage, bus, workspaces

    async def test_backends_are_opened_and_the_worker_stays_up(self) -> None:
        """It is a long-running process: it must not return on its own."""
        worker, storage, bus, workspaces = await self._start()
        try:
            self.assertTrue(storage.entered)
            self.assertTrue(bus.entered)
            self.assertTrue(workspaces.entered)
            self.assertFalse(worker.done())
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_teardown_releases_every_backend(self) -> None:
        """Whatever the worker opened is closed when it unwinds."""
        worker, storage, bus, workspaces = await self._start()

        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        self.assertTrue(storage.exited)
        self.assertTrue(bus.exited)
        self.assertTrue(workspaces.exited)

    @skipIf(
        sys.platform == "win32",
        "Windows event loops do not implement add_signal_handler.",
    )
    async def test_shutdown_signals_are_handled(self) -> None:
        """A container stops the worker with SIGTERM, so both signals
        must be claimed rather than left to kill the process."""
        worker, _, _, _ = await self._start()
        try:
            loop = asyncio.get_running_loop()
            registered = loop._signal_handlers  # pylint: disable=W0212
            self.assertIn(signal.SIGTERM, registered)
            self.assertIn(signal.SIGINT, registered)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
