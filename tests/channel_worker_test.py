# -*- coding: utf-8 -*-
"""Tests for the standalone channel worker's lifecycle.

The worker is the process that owns the platforms' long connections, so
what matters here is that it opens its backends, stays up, and — on the
signal a container sends to stop it — releases them.
"""
import asyncio
import signal
from types import TracebackType
from typing import Any
from unittest import IsolatedAsyncioTestCase

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

    async def test_signal_releases_the_backends(self) -> None:
        """SIGTERM is how a container stops it; every backend it opened
        must be closed on the way out."""
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
        self.assertTrue(storage.entered)
        self.assertTrue(bus.entered)
        self.assertTrue(workspaces.entered)
        self.assertFalse(worker.done())

        signal.raise_signal(signal.SIGTERM)
        await asyncio.wait_for(worker, timeout=2.0)

        self.assertTrue(storage.exited)
        self.assertTrue(bus.exited)
        self.assertTrue(workspaces.exited)
