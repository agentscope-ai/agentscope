# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for :class:`WakeupDispatcher` — one-per-process consumer of the
shared wake-up queue + signal channel.

Verifies the four behaviours that callers rely on:

- Lifecycle is purely ACM: ``__aenter__`` starts the loop and performs
  an initial drain; ``__aexit__`` cancels the loop cleanly.
- A wake-up signal triggers a queue drain; each entry is dispatched as
  a fire-and-forget ``ChatService.run`` call.
- Entries left on the queue from before startup are picked up on
  ``__aenter__`` without waiting for a fresh signal.
- Sessions that are already running are skipped (no duplicate run).
- Malformed entries are logged and skipped, not raised.
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable
from unittest import IsolatedAsyncioTestCase

from agentscope.app._manager import ChatRunRegistry, WakeupDispatcher
from agentscope.app.message_bus import MessageBus


class _FakeStorage:
    """Minimal storage stand-in for the dispatcher's orphan-guard check.

    ``get_session`` returns a truthy sentinel for every session id by
    default; tests that exercise the orphan path mutate
    ``missing_session_ids``.
    """

    def __init__(self) -> None:
        self.missing_session_ids: set[str] = set()

    async def get_session(
        self,
        _user_id: str,
        _agent_id: str,
        session_id: str,
    ) -> object | None:
        """Get a session id from the orphan guard."""
        if session_id in self.missing_session_ids:
            return None
        return object()


class _FakeBus(MessageBus):
    """In-memory bus with just enough behaviour for the dispatcher.

    Implements the four primitives the dispatcher uses
    (``queue_push`` / ``dequeue_wakeups`` indirectly via the parent's
    domain helper / ``subscribe_wakeup_signal`` / ``is_locked`` /
    ``publish``) and stubs the others.
    """

    def __init__(self) -> None:
        self.queues: dict[str, list[tuple[str, dict]]] = {}
        self._channels: dict[str, asyncio.Queue] = {}
        self._next = 0
        self._locks: set[str] = set()

    def _channel(self, key: str) -> asyncio.Queue:
        return self._channels.setdefault(key, asyncio.Queue())

    # Mode A — queue
    async def queue_push(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        self._next += 1
        entry_id = str(self._next)
        self.queues.setdefault(key, []).append((entry_id, payload))
        return entry_id

    async def queue_drain(
        self,
        key: str,
        *,
        max_count: int,
    ) -> list[tuple[str, dict]]:
        entries = self.queues.get(key, [])[:max_count]
        self.queues[key] = self.queues.get(key, [])[max_count:]
        return entries

    async def queue_delete(self, key: str) -> None:
        self.queues.pop(key, None)

    # Mode C — log (unused here)
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

    # Mode E — lock
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
    """Records calls to :meth:`run` so tests can assert dispatch."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.notify = asyncio.Event()

    async def run(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: Any = None,
    ) -> None:
        """Record the call and signal a waiter."""
        self.calls.append(
            {
                "user_id": user_id,
                "session_id": session_id,
                "agent_id": agent_id,
                "input_msg": input_msg,
            },
        )
        self.notify.set()


async def _yield_a_few_times(ticks: int = 8) -> None:
    """Yield the event loop a few times so spawned tasks make progress."""
    for _ in range(ticks):
        await asyncio.sleep(0)


class TestWakeupDispatcherDispatch(IsolatedAsyncioTestCase):
    """Verifies the signal-driven dispatch path."""

    async def test_signal_drives_dispatch(self) -> None:
        """A wake-up signal causes the queue to be drained and each
        entry dispatched as a chat run."""
        bus = _FakeBus()
        chat = _FakeChatService()
        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await bus.queue_push(
                MessageBus._WAKEUP_QUEUE_KEY,
                {"user_id": "u", "session_id": "s1", "agent_id": "a1"},
            )
            await bus.publish(MessageBus._WAKEUP_SIGNAL_KEY, {})

            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)

        self.assertEqual(
            chat.calls,
            [
                {
                    "user_id": "u",
                    "session_id": "s1",
                    "agent_id": "a1",
                    "input_msg": None,
                },
            ],
        )

    async def test_initial_drain_picks_up_pending_entries(self) -> None:
        """Entries on the queue from before ``__aenter__`` are picked up
        without waiting for a fresh signal."""
        bus = _FakeBus()
        chat = _FakeChatService()
        await bus.queue_push(
            MessageBus._WAKEUP_QUEUE_KEY,
            {"user_id": "u", "session_id": "pre", "agent_id": "a"},
        )

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await _yield_a_few_times()

        self.assertEqual(
            chat.calls,
            [
                {
                    "user_id": "u",
                    "session_id": "pre",
                    "agent_id": "a",
                    "input_msg": None,
                },
            ],
        )

    async def test_active_session_skipped(self) -> None:
        """If the target session is already running, no chat run is
        spawned for it."""
        bus = _FakeBus()
        chat = _FakeChatService()
        bus._locks.add(MessageBus._SESSION_LOCK_KEY.format(sid="busy"))

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await bus.queue_push(
                MessageBus._WAKEUP_QUEUE_KEY,
                {"user_id": "u", "session_id": "busy", "agent_id": "a"},
            )
            await bus.publish(MessageBus._WAKEUP_SIGNAL_KEY, {})
            await asyncio.sleep(0.05)

        self.assertEqual(chat.calls, [])

    async def test_malformed_entry_skipped(self) -> None:
        """A wake-up entry missing required fields is logged and skipped,
        not raised; later valid entries still dispatch."""
        bus = _FakeBus()
        chat = _FakeChatService()

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await bus.queue_push(
                MessageBus._WAKEUP_QUEUE_KEY,
                {"oops": True},
            )
            await bus.queue_push(
                MessageBus._WAKEUP_QUEUE_KEY,
                {"user_id": "u", "session_id": "s2", "agent_id": "a"},
            )
            await bus.publish(MessageBus._WAKEUP_SIGNAL_KEY, {})
            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)

        # Only the valid entry made it through.
        self.assertEqual(
            chat.calls,
            [
                {
                    "user_id": "u",
                    "session_id": "s2",
                    "agent_id": "a",
                    "input_msg": None,
                },
            ],
        )

    async def test_deleted_session_skipped(self) -> None:
        """A wake-up whose target session no longer exists in storage
        is dropped without spawning a chat run; later wake-ups for live
        sessions still dispatch."""
        bus = _FakeBus()
        chat = _FakeChatService()
        storage = _FakeStorage()
        storage.missing_session_ids.add("ghost")

        async with WakeupDispatcher(
            message_bus=bus,
            storage=storage,
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await bus.queue_push(
                MessageBus._WAKEUP_QUEUE_KEY,
                {"user_id": "u", "session_id": "ghost", "agent_id": "a"},
            )
            await bus.queue_push(
                MessageBus._WAKEUP_QUEUE_KEY,
                {"user_id": "u", "session_id": "live", "agent_id": "a"},
            )
            await bus.publish(MessageBus._WAKEUP_SIGNAL_KEY, {})
            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)

        self.assertEqual(
            chat.calls,
            [
                {
                    "user_id": "u",
                    "session_id": "live",
                    "agent_id": "a",
                    "input_msg": None,
                },
            ],
        )


class TestWakeupDispatcherLifecycle(IsolatedAsyncioTestCase):
    """Tests covering the ``__aenter__`` / ``__aexit__`` ACM behaviour."""

    async def test_exit_cancels_loop_cleanly(self) -> None:
        """``__aexit__`` cancels the dispatcher's loop task and returns
        without re-raising the cancellation."""
        bus = _FakeBus()
        chat = _FakeChatService()
        dispatcher = WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        )

        # pylint: disable=unnecessary-dunder-call
        await dispatcher.__aenter__()
        loop_task = dispatcher._task
        self.assertIsNotNone(loop_task)

        await dispatcher.__aexit__(None, None, None)

        self.assertIsNone(dispatcher._task)
        self.assertTrue(loop_task.cancelled() or loop_task.done())
