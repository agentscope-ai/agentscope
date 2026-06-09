# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for :class:`CancelDispatcher` — one-per-process consumer of the
shared session-cancel broadcast channel.

Verifies that on each incoming ``session_id`` the dispatcher:

- Cancels the chat-run task in :class:`ChatRunRegistry` when it owns one
  locally.
- Asks :class:`BackgroundTaskManager` to cancel local BG tasks for the
  same session.
- Silently does nothing for sessions whose state lives on other
  processes.
"""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Callable
from unittest import IsolatedAsyncioTestCase

from agentscope.app._manager import (
    BackgroundTaskManager,
    CancelDispatcher,
    ChatRunRegistry,
)
from agentscope.app.message_bus import MessageBus


class _FakeBus(MessageBus):
    """In-memory bus with just enough behaviour for the dispatcher.

    Only the cancel-broadcast channel is exercised here; the other
    primitives are stubbed.
    """

    def __init__(self) -> None:
        self._channels: dict[str, asyncio.Queue] = {}
        self._locks: set[str] = set()

    def _channel(self, key: str) -> asyncio.Queue:
        return self._channels.setdefault(key, asyncio.Queue())

    # Mode A — queue (unused)
    async def queue_push(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        return "n/a"

    async def queue_drain(
        self,
        key: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        return []

    async def queue_delete(self, key: str) -> None:
        return None

    # Mode C — log (unused)
    async def log_append(
        self,
        key: str,
        payload: dict,
        *,
        max_len: int | None = None,
        ttl_secs: int | None = None,
    ) -> str:
        return "n/a"

    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        return []

    async def log_trim(
        self,
        key: str,
        before_id: str | None = None,
    ) -> None:
        return None

    # Mode D — pub/sub
    async def publish(self, key: str, payload: dict) -> None:
        await self._channel(key).put(payload)

    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        if on_ready is not None:
            on_ready()
        while True:
            yield await self._channel(key).get()

    # Mode E — lock (unused)
    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> AsyncGenerator[None, None]:
        self._locks.add(key)
        try:
            yield
        finally:
            self._locks.discard(key)

    async def is_locked(self, key: str) -> bool:
        return key in self._locks


async def _yield_a_few_times(ticks: int = 8) -> None:
    """Yield the event loop a few times so spawned tasks make progress."""
    for _ in range(ticks):
        await asyncio.sleep(0)


class _NeverEndingCoro:
    """Helper: yields a fresh coroutine that sleeps forever."""

    @staticmethod
    async def run() -> None:
        """The fake coroutine."""
        await asyncio.Event().wait()


class TestCancelDispatcher(IsolatedAsyncioTestCase):
    """Verifies the cross-process cancel fan-out."""

    async def test_cancel_signal_cancels_local_chat_run(self) -> None:
        """Broadcast for a session whose chat run is registered locally
        cancels the registered asyncio task."""
        bus = _FakeBus()
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager()

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ):
            chat_task = registry.spawn(
                _NeverEndingCoro.run(),
                session_id="sess-A",
            )
            await bus.session_publish_cancel("sess-A")

            # Wait until the cancel actually propagates.
            for _ in range(50):
                if chat_task.cancelled() or chat_task.done():
                    break
                await asyncio.sleep(0.01)

        self.assertTrue(chat_task.cancelled() or chat_task.done())

    async def test_cancel_signal_cancels_local_bg_tasks(self) -> None:
        """Broadcast for a session with locally-registered BG tasks
        cancels each of them; tasks for other sessions are untouched."""
        bus = _FakeBus()
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager()

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ):
            bg_task_a1 = asyncio.create_task(_NeverEndingCoro.run())
            bg_task_a2 = asyncio.create_task(_NeverEndingCoro.run())
            bg_task_b = asyncio.create_task(_NeverEndingCoro.run())

            await bg_manager.register_task(
                bg_task_a1,
                session_id="sess-A",
                agent_id="agent-A",
                user_id="u",
            )
            await bg_manager.register_task(
                bg_task_a2,
                session_id="sess-A",
                agent_id="agent-A",
                user_id="u",
            )
            await bg_manager.register_task(
                bg_task_b,
                session_id="sess-B",
                agent_id="agent-B",
                user_id="u",
            )

            await bus.session_publish_cancel("sess-A")

            for _ in range(50):
                if bg_task_a1.cancelled() and bg_task_a2.cancelled():
                    break
                await asyncio.sleep(0.01)

        self.assertTrue(bg_task_a1.cancelled() or bg_task_a1.done())
        self.assertTrue(bg_task_a2.cancelled() or bg_task_a2.done())
        # sess-B BG task is left running until shutdown cancels it.
        self.assertFalse(bg_task_b.cancelled())
        bg_task_b.cancel()

    async def test_cancel_signal_for_remote_session_is_noop(self) -> None:
        """Broadcast for a session held on another process is silently
        ignored — no exception, no spurious cancel."""
        bus = _FakeBus()
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager()

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ):
            # Register an unrelated chat run + unrelated BG task so we
            # can verify the unrelated work survives the broadcast.
            unrelated_chat = registry.spawn(
                _NeverEndingCoro.run(),
                session_id="other",
            )
            unrelated_bg = asyncio.create_task(_NeverEndingCoro.run())
            await bg_manager.register_task(
                unrelated_bg,
                session_id="other",
                agent_id="agent",
                user_id="u",
            )

            await bus.session_publish_cancel("not-on-this-process")
            await _yield_a_few_times()

            self.assertFalse(unrelated_chat.cancelled())
            self.assertFalse(unrelated_bg.cancelled())

        # __aexit__ of ChatRunRegistry + BackgroundTaskManager cancels
        # the unrelated tasks on shutdown.

    async def test_cancel_fans_out_to_both_chat_and_bg_in_one_signal(
        self,
    ) -> None:
        """A single cancel broadcast cancels both the local chat run
        and the local BG task(s) for the session, not just one."""
        bus = _FakeBus()
        registry = ChatRunRegistry()
        bg_manager = BackgroundTaskManager()

        async with bg_manager, registry, CancelDispatcher(
            message_bus=bus,
            registry=registry,
            bg_manager=bg_manager,
        ):
            chat_task = registry.spawn(
                _NeverEndingCoro.run(),
                session_id="sess-X",
            )
            bg_task = asyncio.create_task(_NeverEndingCoro.run())
            await bg_manager.register_task(
                bg_task,
                session_id="sess-X",
                agent_id="agent-X",
                user_id="u",
            )

            await bus.session_publish_cancel("sess-X")

            for _ in range(50):
                if chat_task.cancelled() and bg_task.cancelled():
                    break
                await asyncio.sleep(0.01)

        self.assertTrue(chat_task.cancelled() or chat_task.done())
        self.assertTrue(bg_task.cancelled() or bg_task.done())


class TestBackgroundTaskManagerCancelSessionTasks(IsolatedAsyncioTestCase):
    """Verifies :meth:`BackgroundTaskManager.cancel_session_tasks`."""

    async def test_cancels_only_matching_session(self) -> None:
        """Only tasks whose ``session_id`` matches are cancelled; the
        return value reports the local count."""
        bg_manager = BackgroundTaskManager()
        async with bg_manager:
            task_a = asyncio.create_task(_NeverEndingCoro.run())
            task_b = asyncio.create_task(_NeverEndingCoro.run())
            await bg_manager.register_task(
                task_a,
                session_id="match",
                agent_id="a",
                user_id="u",
            )
            await bg_manager.register_task(
                task_b,
                session_id="other",
                agent_id="b",
                user_id="u",
            )

            count = bg_manager.cancel_session_tasks("match")
            self.assertEqual(count, 1)

            for _ in range(50):
                if task_a.cancelled():
                    break
                await asyncio.sleep(0.01)

            self.assertTrue(task_a.cancelled())
            self.assertFalse(task_b.cancelled())

    async def test_no_matches_returns_zero(self) -> None:
        """A session with no locally-registered tasks returns 0 and
        does no work."""
        bg_manager = BackgroundTaskManager()
        async with bg_manager:
            task = asyncio.create_task(_NeverEndingCoro.run())
            await bg_manager.register_task(
                task,
                session_id="other",
                agent_id="a",
                user_id="u",
            )

            self.assertEqual(
                bg_manager.cancel_session_tasks("ghost"),
                0,
            )
            self.assertFalse(task.cancelled())
