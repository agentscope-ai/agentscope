# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for WakeupDispatcher — single per-process consumer of
the shared wakeup queue + signal."""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.app._manager import WakeupDispatcher
from agentscope.app.message_bus import MessageBus


class _FakeBus(MessageBus):
    """In-memory bus with queue + signal semantics that match what
    the dispatcher relies on."""

    def __init__(self) -> None:
        self.queues: dict[str, list[tuple[str, dict]]] = {}
        self._channels: dict[str, asyncio.Queue] = {}
        self._next = 0
        self._locks: set[str] = set()

    def _channel(self, key: str) -> asyncio.Queue:
        return self._channels.setdefault(key, asyncio.Queue())

    def _alloc_id(self) -> str:
        self._next += 1
        return f"id-{self._next}"

    async def queue_push(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        entry_id = self._alloc_id()
        self.queues.setdefault(key, []).append((entry_id, payload))
        return entry_id

    async def queue_drain(
        self,
        key: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        entries = self.queues.get(key, [])
        head = entries[:max_count]
        self.queues[key] = entries[max_count:]
        return head

    async def log_append(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
        max_len: int | None = None,
    ) -> str:
        return ""

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
        pass

    async def publish(self, key: str, payload: dict) -> None:
        await self._channel(key).put(payload)

    async def subscribe(
        self,
        key: str,
        *,
        on_ready=None,
    ) -> AsyncGenerator[dict, None]:
        q = self._channel(key)
        if on_ready is not None:
            on_ready()
        while True:
            yield await q.get()

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


class _FakeChatService:
    """Records every run() call; signals an event for test waits."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.notify = asyncio.Event()

    async def run(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg=None,
    ) -> None:
        self.calls.append(
            {
                "user_id": user_id,
                "session_id": session_id,
                "agent_id": agent_id,
                "input_msg": input_msg,
            },
        )
        self.notify.set()


class TestWakeupDispatcher(IsolatedAsyncioTestCase):
    """Verifies the dispatch loop + initial drain + idle-skip."""

    async def asyncSetUp(self) -> None:
        """Build fakes + a dispatcher (not started yet)."""
        self.bus = _FakeBus()
        self.chat = _FakeChatService()
        self.dispatcher = WakeupDispatcher(
            message_bus=self.bus,
            chat_service=self.chat,
        )

    async def asyncTearDown(self) -> None:
        """Make sure the loop stops between tests."""
        await self.dispatcher.stop()

    async def _drain_spawned(self) -> None:
        """Wait briefly so any background ChatService.run tasks have
        a chance to record their call."""
        for _ in range(5):
            await asyncio.sleep(0)

    async def test_signal_drives_dispatch(self) -> None:
        """A wakeup signal causes the queue to be drained and each
        entry dispatched as a chat run."""
        await self.dispatcher.start()
        await self.bus.queue_push(
            MessageBus._WAKEUP_QUEUE_KEY,
            {"user_id": "u", "session_id": "s1", "agent_id": "a1"},
        )
        await self.bus.publish(MessageBus._WAKEUP_SIGNAL_KEY, {})

        await asyncio.wait_for(self.chat.notify.wait(), timeout=2.0)
        self.assertEqual(len(self.chat.calls), 1)
        self.assertEqual(self.chat.calls[0]["session_id"], "s1")
        self.assertEqual(self.chat.calls[0]["user_id"], "u")
        self.assertEqual(self.chat.calls[0]["agent_id"], "a1")
        self.assertIsNone(self.chat.calls[0]["input_msg"])

    async def test_initial_drain_picks_up_pending_entries(self) -> None:
        """Entries left in the queue from before start() are picked
        up on startup without waiting for a fresh signal."""
        await self.bus.queue_push(
            MessageBus._WAKEUP_QUEUE_KEY,
            {"user_id": "u", "session_id": "s_pre", "agent_id": "a"},
        )
        await self.dispatcher.start()
        await self._drain_spawned()
        self.assertEqual(len(self.chat.calls), 1)
        self.assertEqual(self.chat.calls[0]["session_id"], "s_pre")

    async def test_active_session_skipped(self) -> None:
        """If the session is already running, the dispatcher does
        not spawn a duplicate run."""
        # Simulate an active session by holding its lock on the bus.
        self.bus._locks.add(
            MessageBus._SESSION_LOCK_KEY.format(sid="s1"),
        )
        await self.dispatcher.start()
        await self.bus.queue_push(
            MessageBus._WAKEUP_QUEUE_KEY,
            {"user_id": "u", "session_id": "s1", "agent_id": "a"},
        )
        await self.bus.publish(MessageBus._WAKEUP_SIGNAL_KEY, {})
        await asyncio.sleep(0.05)
        self.assertEqual(self.chat.calls, [])

    async def test_malformed_entry_skipped(self) -> None:
        """A wakeup entry missing required fields is logged + skipped,
        not raised."""
        await self.dispatcher.start()
        await self.bus.queue_push(MessageBus._WAKEUP_QUEUE_KEY, {"oops": True})
        await self.bus.queue_push(
            MessageBus._WAKEUP_QUEUE_KEY,
            {"user_id": "u", "session_id": "s2", "agent_id": "a"},
        )
        await self.bus.publish(MessageBus._WAKEUP_SIGNAL_KEY, {})
        await asyncio.wait_for(self.chat.notify.wait(), timeout=2.0)

        # Only the valid entry made it through.
        self.assertEqual(len(self.chat.calls), 1)
        self.assertEqual(self.chat.calls[0]["session_id"], "s2")

    async def test_stop_cancels_loop(self) -> None:
        """stop() cancels the dispatcher task cleanly."""
        await self.dispatcher.start()
        task = self.dispatcher._task
        self.assertIsNotNone(task)
        await self.dispatcher.stop()
        self.assertIsNone(self.dispatcher._task)
        self.assertTrue(task.cancelled() or task.done())
