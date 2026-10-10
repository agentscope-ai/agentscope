# -*- coding: utf-8 -*-
"""Tests for the in-process chat-run registry."""
import asyncio
import inspect
from unittest import IsolatedAsyncioTestCase

from agentscope.app._manager import ChatRunRegistry


class TestChatRunRegistry(IsolatedAsyncioTestCase):
    """Verify ownership of coroutines passed to the registry."""

    async def test_duplicate_spawn_closes_rejected_coroutine(self) -> None:
        """A rejected run must not leave an unawaited coroutine behind."""
        registry = ChatRunRegistry()
        started = asyncio.Event()
        release = asyncio.Event()

        async def active_run() -> None:
            started.set()
            await release.wait()

        async def rejected_run() -> None:
            self.fail("Rejected run must not execute")

        active_task = registry.spawn(active_run(), session_id="session")
        await started.wait()
        rejected = rejected_run()

        try:
            with self.assertRaisesRegex(RuntimeError, "active chat run"):
                registry.spawn(rejected, session_id="session")
            self.assertEqual(
                inspect.getcoroutinestate(rejected),
                inspect.CORO_CLOSED,
            )
            self.assertIs(registry.get("session"), active_task)
        finally:
            rejected.close()
            release.set()
            await active_task
