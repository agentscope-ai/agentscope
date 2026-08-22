# -*- coding: utf-8 -*-
# pylint: disable=protected-access,missing-function-docstring,unused-argument
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
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Callable
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from agentscope.app._manager import ChatRunRegistry, WakeupDispatcher
from agentscope.app._service import ChatService
from agentscope.app._bus_ops import (
    ChatQueueBusyError,
    ChatQueueFullError,
    ChatQueuePayloadTooLargeError,
    delete_chat_input,
    enqueue_chat_input,
    enqueue_run_trigger,
    list_chat_inputs,
    reorder_chat_inputs,
    update_chat_input,
)
from agentscope.app.message_bus import MessageBus, MessageBusKeys
from agentscope.app._router._chat import chat as chat_endpoint
from agentscope.app._router._schema import (
    ChatRequest,
    UpdateChatQueueItemRequest,
)
from agentscope.message import AssistantMsg, ToolCallBlock, UserMsg


class _FakeStorage:
    """Minimal storage stand-in for the dispatcher's orphan-guard check.

    ``get_session`` returns a truthy sentinel for every session id by
    default; tests that exercise the orphan path mutate
    ``missing_session_ids``.
    """

    def __init__(self) -> None:
        self.missing_session_ids: set[str] = set()
        self.parked_session_ids: set[str] = set()
        self.persisted_messages: list[tuple[str, str, object]] = []

    async def get_session(
        self,
        _user_id: str,
        _agent_id: str,
        session_id: str,
    ) -> object | None:
        """Get a session id from the orphan guard."""
        if session_id in self.missing_session_ids:
            return None
        context = []
        if session_id in self.parked_session_ids:
            context = [
                AssistantMsg(
                    name="agent",
                    content=[
                        ToolCallBlock(
                            id="tc",
                            name="needs_confirmation",
                            input="{}",
                            state="asking",
                        ),
                    ],
                ),
            ]
        return SimpleNamespace(state=SimpleNamespace(context=context))

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        message: object,
    ) -> None:
        """Record messages durably persisted before queued execution."""
        self.persisted_messages.append((user_id, session_id, message))


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
        self.log_appends: list[tuple[str, dict]] = []
        self.registries: dict[str, dict[str, str]] = {}
        self.wakeup_drain_task_names: list[str] = []

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
        if key == MessageBusKeys.wakeup_queue():
            task = asyncio.current_task()
            self.wakeup_drain_task_names.append(
                task.get_name() if task is not None else "",
            )
        entries = self.queues.get(key, [])[:max_count]
        self.queues[key] = self.queues.get(key, [])[max_count:]
        return entries

    async def queue_read(
        self,
        key: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        return list(self.queues.get(key, [])[:max_count])

    async def queue_replace(
        self,
        key: str,
        payloads: list[dict],
    ) -> list[str]:
        entries = []
        for payload in payloads:
            self._next += 1
            entries.append((str(self._next), payload))
        self.queues[key] = entries
        return [entry_id for entry_id, _payload in entries]

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
        self.log_appends.append((key, payload))
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

    async def try_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> bool:
        return True

    async def unlock(self, key: str) -> None:
        pass

    # Mode F — registry
    async def registry_set(
        self,
        namespace: str,
        field: str,
        value: str,
        *,
        ttl_secs: int | None = None,
    ) -> None:
        self.registries.setdefault(namespace, {})[field] = value

    async def registry_del(self, namespace: str, field: str) -> None:
        self.registries.get(namespace, {}).pop(field, None)

    async def registry_exists(self, namespace: str, field: str) -> bool:
        return field in self.registries.get(namespace, {})

    async def registry_getall(self, namespace: str) -> dict[str, str]:
        return dict(self.registries.get(namespace, {}))

    async def registry_get(
        self,
        namespace: str,
        field: str,
    ) -> str | None:
        return self.registries.get(namespace, {}).get(field)

    async def registry_drop(self, namespace: str) -> None:
        self.registries.pop(namespace, None)


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


class _BlockingChatService(_FakeChatService):
    """Holds the first turn so later submissions exercise FIFO queuing."""

    def __init__(self, bus: MessageBus) -> None:
        super().__init__()
        self._bus = bus
        self.release_first = asyncio.Event()

    async def run(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: Any = None,
    ) -> None:
        await super().run(user_id, session_id, agent_id, input_msg)
        if len(self.calls) == 1:
            await self.release_first.wait()
        await enqueue_run_trigger(
            self._bus,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            kind=MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE,
        )


async def _yield_a_few_times(ticks: int = 8) -> None:
    """Yield the event loop a few times so spawned tasks make progress."""
    for _ in range(ticks):
        await asyncio.sleep(0)


class TestWakeupDispatcherDispatch(  # pylint: disable=too-many-public-methods
    IsolatedAsyncioTestCase,
):
    """Verifies the signal-driven dispatch path."""

    async def test_run_trigger_can_be_batched_without_signal(self) -> None:
        """Recovery can reuse canonical serialization and signal once."""
        bus = _FakeBus()

        await enqueue_run_trigger(
            bus,
            user_id="u",
            session_id="s",
            agent_id="a",
            kind=MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE,
            signal=False,
        )

        triggers = await bus.queue_read(
            MessageBusKeys.wakeup_queue(),
            max_count=10,
        )
        self.assertEqual(
            triggers[0][1],
            {
                "user_id": "u",
                "session_id": "s",
                "agent_id": "a",
                "kind": MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE,
                "input": None,
            },
        )
        self.assertEqual(
            bus._channel(MessageBusKeys.wakeup_signal()).qsize(),
            0,
        )

    async def test_direct_message_and_queued_message_are_distinct(
        self,
    ) -> None:
        """Channel messages carry input instead of draining the web FIFO."""
        bus = _FakeBus()
        chat = _FakeChatService()
        message = UserMsg("channel-user", "from channel", id="channel-msg")

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await enqueue_run_trigger(
                bus,
                user_id="u",
                session_id="channel-session",
                agent_id="a",
                kind=MessageBusKeys.WAKEUP_KIND_MESSAGE,
                inputs=message,
            )
            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)

        self.assertEqual(
            chat.calls,
            [
                {
                    "user_id": "u",
                    "session_id": "channel-session",
                    "agent_id": "a",
                    "input_msg": message,
                },
            ],
        )

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
                MessageBusKeys.wakeup_queue(),
                {"user_id": "u", "session_id": "s1", "agent_id": "a1"},
            )
            await bus.publish(MessageBusKeys.wakeup_signal(), {})

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
            MessageBusKeys.wakeup_queue(),
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

    async def test_active_session_not_spawned_while_locked(self) -> None:
        """While the target session holds its run lock, no chat run is
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
                MessageBusKeys.wakeup_queue(),
                {"user_id": "u", "session_id": "busy", "agent_id": "a"},
            )
            await bus.publish(MessageBusKeys.wakeup_signal(), {})
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
                MessageBusKeys.wakeup_queue(),
                {"oops": True},
            )
            await bus.queue_push(
                MessageBusKeys.wakeup_queue(),
                {"user_id": "u", "session_id": "s2", "agent_id": "a"},
            )
            await bus.publish(MessageBusKeys.wakeup_signal(), {})
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
                MessageBusKeys.wakeup_queue(),
                {"user_id": "u", "session_id": "ghost", "agent_id": "a"},
            )
            await bus.queue_push(
                MessageBusKeys.wakeup_queue(),
                {"user_id": "u", "session_id": "live", "agent_id": "a"},
            )
            await bus.publish(MessageBusKeys.wakeup_signal(), {})
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

    async def test_wrong_user_message_trigger_never_deletes_queue(
        self,
    ) -> None:
        """A trigger-scoped ownership miss only drops that trigger."""

        class _OwnerStorage(_FakeStorage):
            async def get_session(
                self,
                user_id: str,
                _agent_id: str,
                _session_id: str,
            ) -> object | None:
                return object() if user_id == "owner" else None

        bus = _FakeBus()
        chat = _FakeChatService()
        existing = await enqueue_chat_input(
            bus,
            "owner",
            "shared-sid",
            "a",
            UserMsg("user", "must survive"),
        )
        await bus.queue_drain(MessageBusKeys.wakeup_queue(), max_count=10)
        await bus.registry_del(
            MessageBusKeys.chat_input_pending_registry(),
            "shared-sid",
        )
        await bus.queue_push(
            MessageBusKeys.wakeup_queue(),
            {
                "user_id": "attacker",
                "session_id": "shared-sid",
                "agent_id": "a",
                "kind": MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE,
                "input": None,
            },
        )

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_OwnerStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await _yield_a_few_times()

        self.assertEqual(chat.calls, [])
        self.assertEqual(
            [item["id"] for item in await list_chat_inputs(bus, "shared-sid")],
            [existing["id"]],
        )

    async def test_empty_queued_list_is_dropped_without_stopping_pump(
        self,
    ) -> None:
        """A legacy malformed item cannot crash or strand later turns."""
        bus = _FakeBus()
        chat = _FakeChatService()
        queue_key = MessageBusKeys.chat_inputs("s")
        base = {
            "user_id": "u",
            "session_id": "s",
            "agent_id": "a",
            "created_at": "2026-01-01T00:00:00Z",
        }
        await bus.queue_push(queue_key, {**base, "id": "empty", "input": []})
        valid = UserMsg("user", "after malformed")
        await bus.queue_push(
            queue_key,
            {
                **base,
                "id": "valid",
                "input": valid.model_dump(mode="json"),
            },
        )
        await enqueue_run_trigger(
            bus,
            user_id="u",
            session_id="s",
            agent_id="a",
            kind=MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE,
        )

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)

        self.assertEqual(len(chat.calls), 1)
        self.assertEqual(chat.calls[0]["input_msg"].id, valid.id)

    async def test_claim_retries_when_input_persistence_failed(self) -> None:
        """A pre-persistence failure leaves a durable, retryable claim."""

        class _FailOnceStorage(_FakeStorage):
            def __init__(self) -> None:
                super().__init__()
                self.fail_once = True

            async def upsert_message(
                self,
                user_id: str,
                session_id: str,
                message: object,
            ) -> None:
                if self.fail_once:
                    self.fail_once = False
                    raise RuntimeError("storage unavailable")
                await super().upsert_message(user_id, session_id, message)

        bus = _FakeBus()
        storage = _FailOnceStorage()
        chat = _FakeChatService()
        dispatcher = WakeupDispatcher(
            message_bus=bus,
            storage=storage,
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        )
        await enqueue_chat_input(
            bus,
            "u",
            "claim-retry",
            "a",
            UserMsg("user", "persist me", id="claim-message"),
        )

        with self.assertRaises(RuntimeError):
            await dispatcher._drain_chat_inputs("u", "claim-retry", "a")

        self.assertEqual(
            await bus.queue_read(
                MessageBusKeys.chat_inputs("claim-retry"),
                max_count=1,
            ),
            [],
        )
        claim = json.loads(
            (
                await bus.registry_getall(
                    MessageBusKeys.chat_input_inflight_registry(),
                )
            )["claim-retry"],
        )
        self.assertEqual(claim["state"], "claimed")

        self.assertTrue(
            await dispatcher._drain_chat_inputs("u", "claim-retry", "a"),
        )
        self.assertEqual(len(chat.calls), 1)
        self.assertEqual(chat.calls[0]["input_msg"].id, "claim-message")
        self.assertFalse(
            await bus.registry_exists(
                MessageBusKeys.chat_input_inflight_registry(),
                "claim-retry",
            ),
        )

    async def test_recovered_post_persistence_claim_is_not_replayed(
        self,
    ) -> None:
        """Unknown tool side effects turn a recovered claim into failure."""
        bus = _FakeBus()
        storage = _FakeStorage()
        chat = _FakeChatService()
        dispatcher = WakeupDispatcher(
            message_bus=bus,
            storage=storage,
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        )
        await enqueue_chat_input(
            bus,
            "u",
            "claim-failed",
            "a",
            UserMsg("user", "do not replay", id="persisted-message"),
        )
        payload, _state = await dispatcher._claim_chat_input("claim-failed")
        await dispatcher._set_chat_input_claim_state(
            "claim-failed",
            payload,
            "input_persisted",
        )

        self.assertTrue(
            await dispatcher._drain_chat_inputs("u", "claim-failed", "a"),
        )
        self.assertEqual(chat.calls, [])
        self.assertIn(
            "chat_input_failed",
            [event.get("name") for _key, event in bus.log_appends],
        )
        self.assertFalse(
            await bus.registry_exists(
                MessageBusKeys.chat_input_inflight_registry(),
                "claim-failed",
            ),
        )

    async def test_claim_written_before_drain_does_not_duplicate_turn(
        self,
    ) -> None:
        """Recovery consumes a still-present claimed head exactly once."""
        bus = _FakeBus()
        chat = _FakeChatService()
        dispatcher = WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        )
        await enqueue_chat_input(
            bus,
            "u",
            "claim-before-drain",
            "a",
            UserMsg("user", "once", id="once"),
        )
        pending = await bus.queue_read(
            MessageBusKeys.chat_inputs("claim-before-drain"),
            max_count=1,
        )
        payload = pending[0][1]
        await bus.registry_set(
            MessageBusKeys.chat_input_inflight_registry(),
            "claim-before-drain",
            json.dumps({"state": "claimed", "payload": payload}),
        )

        self.assertTrue(
            await dispatcher._drain_chat_inputs(
                "u",
                "claim-before-drain",
                "a",
            ),
        )
        self.assertEqual(len(chat.calls), 1)
        self.assertEqual(
            await bus.queue_read(
                MessageBusKeys.chat_inputs("claim-before-drain"),
                max_count=1,
            ),
            [],
        )

    async def test_cancelled_started_turn_emits_terminal_event(self) -> None:
        """At-most-once cancellation is explicit rather than silent."""
        bus = _FakeBus()
        chat = _BlockingChatService(bus)
        registry = ChatRunRegistry()

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=registry,
        ):
            await enqueue_chat_input(
                bus,
                "u",
                "cancelled",
                "a",
                UserMsg("user", "interrupt me"),
            )
            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)
            task = registry.get("cancelled")
            self.assertIsNotNone(task)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await _yield_a_few_times()

        event_names = [
            payload.get("name")
            for _key, payload in bus.log_appends
            if payload.get("name") is not None
        ]
        self.assertIn("chat_input_cancelled", event_names)
        self.assertEqual(
            await bus.queue_read(
                MessageBusKeys.chat_inputs("cancelled"),
                max_count=10,
            ),
            [],
        )

    async def test_cancel_event_failure_preserves_task_cancellation(
        self,
    ) -> None:
        """A broken event transport cannot replace ``CancelledError``."""

        class _FailingCancelledEventBus(_FakeBus):
            async def log_append(
                self,
                key: str,
                payload: dict,
                *,
                max_len: int | None = None,
                ttl_secs: int | None = None,
            ) -> str:
                if payload.get("name") == "chat_input_cancelled":
                    raise RuntimeError("event transport unavailable")
                return await super().log_append(
                    key,
                    payload,
                    max_len=max_len,
                    ttl_secs=ttl_secs,
                )

        bus = _FailingCancelledEventBus()
        chat = _BlockingChatService(bus)
        registry = ChatRunRegistry()

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=registry,
        ):
            await enqueue_chat_input(
                bus,
                "u",
                "cancel-event-failure",
                "a",
                UserMsg("user", "interrupt me"),
            )
            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)
            task = registry.get("cancel-event-failure")
            self.assertIsNotNone(task)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.assertTrue(task.cancelled())

    async def test_resume_idle_spawns_with_parsed_event(self) -> None:
        """A ``resume`` trigger for an idle session spawns a run whose
        ``input_msg`` is the carried HITL event, rebuilt from its dump."""
        from agentscope.event import UserConfirmResultEvent

        bus = _FakeBus()
        chat = _FakeChatService()
        event = UserConfirmResultEvent.model_construct(
            reply_id="r1",
            confirm_results=[],
        )

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await bus.queue_push(
                MessageBusKeys.wakeup_queue(),
                {
                    "user_id": "u",
                    "session_id": "w1",
                    "agent_id": "wa1",
                    "kind": MessageBusKeys.WAKEUP_KIND_RESUME,
                    "input": event.model_dump(mode="json"),
                },
            )
            await bus.publish(MessageBusKeys.wakeup_signal(), {})
            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)

        self.assertEqual(len(chat.calls), 1)
        call = chat.calls[0]
        self.assertEqual(call["session_id"], "w1")
        self.assertIsInstance(call["input_msg"], UserConfirmResultEvent)
        self.assertEqual(call["input_msg"].reply_id, "r1")

    async def test_resume_running_session_requeues_until_free(self) -> None:
        """A ``resume`` whose target is still running is NOT dropped: it
        is re-queued (with backoff) and dispatched once the session lock
        releases. This is the structural fix for the parked-run 409 race.
        """
        from agentscope.event import UserConfirmResultEvent

        bus = _FakeBus()
        chat = _FakeChatService()
        lock_key = MessageBus._SESSION_LOCK_KEY.format(sid="w1")
        bus._locks.add(lock_key)  # session is busy finishing its park tail
        event = UserConfirmResultEvent.model_construct(
            reply_id="r1",
            confirm_results=[],
        )

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await bus.queue_push(
                MessageBusKeys.wakeup_queue(),
                {
                    "user_id": "u",
                    "session_id": "w1",
                    "agent_id": "wa1",
                    "kind": MessageBusKeys.WAKEUP_KIND_RESUME,
                    "input": event.model_dump(mode="json"),
                },
            )
            await bus.publish(MessageBusKeys.wakeup_signal(), {})

            # While locked, the resume must keep deferring — no run yet.
            await asyncio.sleep(0.25)
            self.assertEqual(chat.calls, [])

            # Release the lock; the re-queued resume now lands.
            bus._locks.discard(lock_key)
            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)

        self.assertEqual(len(chat.calls), 1)
        self.assertEqual(chat.calls[0]["session_id"], "w1")
        self.assertIsInstance(
            chat.calls[0]["input_msg"],
            UserConfirmResultEvent,
        )

    async def test_pending_messages_can_change_during_current_reply(
        self,
    ) -> None:
        """Later turns remain editable and reorderable during generation."""
        bus = _FakeBus()
        chat = _BlockingChatService(bus)

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            first = UserMsg("user", "first", id="m1")
            second = UserMsg("user", "second", id="m2")
            third = UserMsg("user", "third", id="m3")

            await enqueue_chat_input(bus, "u", "s", "a", first)
            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)

            second_item = await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                second,
            )
            third_item = await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                third,
            )
            await asyncio.sleep(0.05)
            self.assertEqual(len(chat.calls), 1)
            self.assertEqual(
                [item["id"] for item in await list_chat_inputs(bus, "s")],
                [second_item["id"], third_item["id"]],
            )

            await update_chat_input(
                bus,
                "u",
                "s",
                "a",
                second_item["id"],
                UserMsg("user", "second edited", id="m2"),
            )
            await reorder_chat_inputs(
                bus,
                "u",
                "s",
                "a",
                [third_item["id"], second_item["id"]],
            )
            chat.release_first.set()
            for _ in range(100):
                if len(chat.calls) == 3:
                    break
                await asyncio.sleep(0.01)

        self.assertEqual(
            [call["input_msg"].id for call in chat.calls],
            ["m1", "m3", "m2"],
        )
        self.assertEqual(
            chat.calls[2]["input_msg"].content[0].text,
            "second edited",
        )
        self.assertEqual(
            await bus.queue_drain(
                MessageBusKeys.chat_inputs("s"),
                max_count=10,
            ),
            [],
        )
        self.assertFalse(
            await bus.registry_exists(
                MessageBusKeys.chat_input_pending_registry(),
                "s",
            ),
        )

    async def test_ordinary_message_waits_for_running_session(self) -> None:
        """A busy message trigger is dropped; the completing run's nudge
        advances the still-durable queue without periodic polling."""
        bus = _FakeBus()
        chat = _FakeChatService()
        lock_key = MessageBusKeys.session_lock("busy-message")
        bus._locks.add(lock_key)

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await enqueue_chat_input(
                bus,
                "u",
                "busy-message",
                "a",
                UserMsg("user", "later", id="queued"),
            )
            await asyncio.sleep(0.25)
            self.assertEqual(chat.calls, [])

            bus._locks.discard(lock_key)
            await enqueue_run_trigger(
                bus,
                user_id="u",
                session_id="busy-message",
                agent_id="a",
                kind=MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE,
            )
            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)

        self.assertEqual(chat.calls[0]["input_msg"].id, "queued")

    async def test_pending_queue_index_survives_dispatcher_restart(
        self,
    ) -> None:
        """Busy-session recovery is durable, not an in-memory timer."""
        bus = _FakeBus()
        chat = _FakeChatService()
        lock_key = MessageBusKeys.session_lock("durable")
        bus._locks.add(lock_key)

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await enqueue_chat_input(
                bus,
                "u",
                "durable",
                "a",
                UserMsg("user", "survive restart"),
            )
            await asyncio.sleep(0.05)

        self.assertTrue(
            await bus.registry_exists(
                MessageBusKeys.chat_input_pending_registry(),
                "durable",
            ),
        )

        bus._locks.discard(lock_key)
        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)

        self.assertEqual(
            chat.calls[0]["input_msg"].content[0].text,
            "survive restart",
        )

    async def test_fallback_tick_drains_trigger_without_live_signal(
        self,
    ) -> None:
        """A lost pub/sub signal cannot strand a durable trigger forever."""
        bus = _FakeBus()
        chat = _FakeChatService()

        with patch(
            "agentscope.app._manager._wakeup_dispatcher."
            "_MESSAGE_FALLBACK_TICK_SECS",
            0.01,
        ):
            async with WakeupDispatcher(
                message_bus=bus,
                storage=_FakeStorage(),
                chat_service=chat,
                chat_run_registry=ChatRunRegistry(),
            ):
                await bus.queue_push(
                    MessageBusKeys.chat_inputs("tick"),
                    {
                        "id": "tick-item",
                        "user_id": "u",
                        "session_id": "tick",
                        "agent_id": "a",
                        "created_at": "2026-01-01T00:00:00Z",
                        "input": UserMsg(
                            "user",
                            "lost signal",
                        ).model_dump(mode="json"),
                    },
                )
                await bus.registry_set(
                    MessageBusKeys.chat_input_pending_registry(),
                    "tick",
                    '{"user_id": "u", "agent_id": "a"}',
                )
                await asyncio.wait_for(chat.notify.wait(), timeout=2.0)

        self.assertEqual(
            chat.calls[0]["input_msg"].content[0].text,
            "lost signal",
        )
        self.assertTrue(bus.wakeup_drain_task_names)
        self.assertEqual(
            set(bus.wakeup_drain_task_names),
            {"wakeup-dispatcher"},
        )

    async def test_ordinary_message_waits_for_hitl_resume(self) -> None:
        """Queued user turns stay behind a parked tool confirmation
        instead of being misinterpreted as its continuation."""
        bus = _FakeBus()
        chat = _FakeChatService()
        storage = _FakeStorage()
        storage.parked_session_ids.add("parked")

        async with WakeupDispatcher(
            message_bus=bus,
            storage=storage,
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await enqueue_chat_input(
                bus,
                "u",
                "parked",
                "a",
                UserMsg("user", "after confirmation", id="after-hitl"),
            )
            await asyncio.sleep(0.25)
            self.assertEqual(chat.calls, [])

            # Simulate the existing resume path completing the parked
            # reply, persisting a clean context tail and issuing its
            # completion nudge.
            storage.parked_session_ids.discard("parked")
            await enqueue_run_trigger(
                bus,
                user_id="u",
                session_id="parked",
                agent_id="a",
                kind=MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE,
            )
            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)

        self.assertEqual(chat.calls[0]["input_msg"].id, "after-hitl")

    async def test_wake_running_session_requeues_until_free(self) -> None:
        """A busy ``wake`` is retained until the inbox can be drained."""
        bus = _FakeBus()
        chat = _FakeChatService()
        lock_key = MessageBusKeys.session_lock("w2")
        bus._locks.add(lock_key)

        async with WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=chat,
            chat_run_registry=ChatRunRegistry(),
        ):
            await bus.queue_push(
                MessageBusKeys.wakeup_queue(),
                {
                    "user_id": "u",
                    "session_id": "w2",
                    "agent_id": "wa2",
                },
            )
            await bus.publish(MessageBusKeys.wakeup_signal(), {})
            await asyncio.sleep(0.25)
            self.assertEqual(chat.calls, [])

            bus._locks.discard(lock_key)
            await asyncio.wait_for(chat.notify.wait(), timeout=2.0)

        self.assertEqual(len(chat.calls), 1)
        self.assertEqual(chat.calls[0]["session_id"], "w2")
        self.assertIsNone(chat.calls[0]["input_msg"])


class TestChatInputClaimReliability(IsolatedAsyncioTestCase):
    """Internal claim transitions favor correctness over UI latency."""

    async def test_transitions_outwait_foreground_mutation_timeout(
        self,
    ) -> None:
        """Claim state cannot fail merely because UI holds the lock."""

        class _SlowMutationLockBus(_FakeBus):
            @asynccontextmanager
            async def acquire_lock(
                self,
                key: str,
                *,
                ttl_secs: int = 600,
            ) -> AsyncGenerator[None, None]:
                del ttl_secs
                if key == MessageBusKeys.chat_input_mutation_lock("s"):
                    await asyncio.sleep(0.02)
                yield

        bus = _SlowMutationLockBus()
        dispatcher = WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=_FakeChatService(),
            chat_run_registry=ChatRunRegistry(),
        )
        payload = {"id": "queued", "input": {}}

        with patch.object(
            MessageBusKeys,
            "CHAT_INPUT_MUTATION_TIMEOUT_SECS",
            0.001,
        ):
            await dispatcher._set_chat_input_claim_state(
                "s",
                payload,
                "input_persisted",
            )
            self.assertTrue(
                await bus.registry_exists(
                    MessageBusKeys.chat_input_inflight_registry(),
                    "s",
                ),
            )
            await dispatcher._finish_chat_input_claim("s")

        self.assertFalse(
            await bus.registry_exists(
                MessageBusKeys.chat_input_inflight_registry(),
                "s",
            ),
        )


class TestWakeupDispatcherLifecycle(IsolatedAsyncioTestCase):
    """Tests covering the ``__aenter__`` / ``__aexit__`` ACM behaviour."""

    async def test_recovery_sweep_has_one_cluster_leader(self) -> None:
        """Two processes sharing a bus do not both run the O(N) sweep."""

        class _CountingDispatcher(WakeupDispatcher):
            sweep_count = 0

            async def _recover_pending_chat_inputs_locked(self) -> int:
                type(self).sweep_count += 1
                return await super()._recover_pending_chat_inputs_locked()

        bus = _FakeBus()
        first = _CountingDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=_FakeChatService(),
            chat_run_registry=ChatRunRegistry(),
        )
        second = _CountingDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=_FakeChatService(),
            chat_run_registry=ChatRunRegistry(),
        )

        await asyncio.gather(
            first._recover_pending_chat_inputs(),
            second._recover_pending_chat_inputs(),
        )

        self.assertEqual(_CountingDispatcher.sweep_count, 1)
        self.assertEqual(
            bus._channel(MessageBusKeys.wakeup_signal()).qsize(),
            0,
        )

    async def test_initial_recovery_does_not_block_startup(self) -> None:
        """An O(N) pending-session sweep runs outside ``__aenter__``."""

        class _SlowRecoveryBus(_FakeBus):
            def __init__(self) -> None:
                super().__init__()
                self.recovery_started = asyncio.Event()
                self.release_recovery = asyncio.Event()
                self._delay_recovery_once = True

            async def registry_getall(
                self,
                namespace: str,
            ) -> dict[str, str]:
                if (
                    namespace == MessageBusKeys.chat_input_recovery_state()
                    and self._delay_recovery_once
                ):
                    self._delay_recovery_once = False
                    self.recovery_started.set()
                    await self.release_recovery.wait()
                return await super().registry_getall(namespace)

        bus = _SlowRecoveryBus()
        dispatcher = WakeupDispatcher(
            message_bus=bus,
            storage=_FakeStorage(),
            chat_service=_FakeChatService(),
            chat_run_registry=ChatRunRegistry(),
        )

        # pylint: disable=unnecessary-dunder-call
        await asyncio.wait_for(dispatcher.__aenter__(), timeout=0.2)
        await asyncio.wait_for(bus.recovery_started.wait(), timeout=0.2)
        bus.release_recovery.set()
        await dispatcher.__aexit__(None, None, None)

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

    async def test_chat_service_strongly_retains_queue_nudge(self) -> None:
        """Detached nudge tasks stay referenced until completion."""
        bus = _FakeBus()
        service = object.__new__(ChatService)
        service._message_bus = bus
        service._queue_nudge_tasks = set()
        await bus.queue_push(
            MessageBusKeys.chat_inputs("s"),
            {"pending": True},
        )

        service.schedule_queue_nudge("u", "s", "a")
        self.assertEqual(len(service._queue_nudge_tasks), 1)
        await _yield_a_few_times()
        self.assertEqual(service._queue_nudge_tasks, set())
        triggers = await bus.queue_read(
            MessageBusKeys.wakeup_queue(),
            max_count=10,
        )
        self.assertEqual(
            triggers[0][1]["kind"],
            MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE,
        )


class TestChatEndpointQueue(IsolatedAsyncioTestCase):
    """Ordinary HTTP chat inputs enter the FIFO instead of spawning."""

    async def test_user_message_is_accepted_into_session_fifo(self) -> None:
        """An ordinary POST is durably accepted instead of directly run."""
        bus = _FakeBus()
        chat_service = _FakeChatService()
        user_msg = UserMsg("user", "queued", id="http-message")

        response = await chat_endpoint(
            request=ChatRequest(
                agent_id="a",
                session_id="s",
                input=user_msg,
            ),
            user_id="u",
            storage=_FakeStorage(),
            chat_service=chat_service,
            chat_run_registry=ChatRunRegistry(),
            message_bus=bus,
        )

        self.assertEqual(response.status, "queued")
        self.assertEqual(chat_service.calls, [])
        queued = await bus.queue_drain(
            MessageBusKeys.chat_inputs("s"),
            max_count=10,
        )
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0][1]["input"]["id"], "http-message")

        triggers = await bus.queue_drain(
            MessageBusKeys.wakeup_queue(),
            max_count=10,
        )
        self.assertEqual(
            triggers[0][1]["kind"],
            MessageBusKeys.WAKEUP_KIND_QUEUED_MESSAGE,
        )

    async def test_cross_user_message_is_rejected_without_queue_damage(
        self,
    ) -> None:
        """POST /chat cannot inject into another user's session queue."""

        class _OwnerStorage(_FakeStorage):
            async def get_session(
                self,
                user_id: str,
                _agent_id: str,
                _session_id: str,
            ) -> object | None:
                return object() if user_id == "owner" else None

        bus = _FakeBus()
        existing = await enqueue_chat_input(
            bus,
            "owner",
            "shared-sid",
            "a",
            UserMsg("user", "keep me", id="existing"),
        )
        with self.assertRaises(HTTPException) as raised:
            await chat_endpoint(
                request=ChatRequest(
                    agent_id="a",
                    session_id="shared-sid",
                    input=UserMsg("user", "inject", id="attack"),
                ),
                user_id="attacker",
                storage=_OwnerStorage(),
                chat_service=_FakeChatService(),
                chat_run_registry=ChatRunRegistry(),
                message_bus=bus,
            )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(
            [item["id"] for item in await list_chat_inputs(bus, "shared-sid")],
            [existing["id"]],
        )

    def test_empty_message_lists_are_rejected_by_request_schemas(
        self,
    ) -> None:
        """Neither enqueue nor PATCH can carry an empty Msg list."""
        with self.assertRaises(ValidationError):
            ChatRequest(agent_id="a", session_id="s", input=[])
        with self.assertRaises(ValidationError):
            UpdateChatQueueItemRequest(
                agent_id="a",
                session_id="s",
                input=[],
            )

    async def test_full_queue_returns_http_429(self) -> None:
        """POST /chat reports backpressure instead of growing forever."""
        bus = _FakeBus()
        with patch.object(MessageBusKeys, "CHAT_INPUT_MAX_LEN", 1):
            await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("user", "already pending"),
            )
            with self.assertRaises(HTTPException) as raised:
                await chat_endpoint(
                    request=ChatRequest(
                        agent_id="a",
                        session_id="s",
                        input=UserMsg("user", "one too many"),
                    ),
                    user_id="u",
                    storage=_FakeStorage(),
                    chat_service=_FakeChatService(),
                    chat_run_registry=ChatRunRegistry(),
                    message_bus=bus,
                )
        self.assertEqual(raised.exception.status_code, 429)

    async def test_oversized_queue_input_returns_http_413(self) -> None:
        """POST /chat exposes the serialized turn size limit."""
        with patch.object(MessageBusKeys, "CHAT_INPUT_MAX_BYTES", 10):
            with self.assertRaises(HTTPException) as raised:
                await chat_endpoint(
                    request=ChatRequest(
                        agent_id="a",
                        session_id="s",
                        input=UserMsg("user", "too large"),
                    ),
                    user_id="u",
                    storage=_FakeStorage(),
                    chat_service=_FakeChatService(),
                    chat_run_registry=ChatRunRegistry(),
                    message_bus=_FakeBus(),
                )
        self.assertEqual(raised.exception.status_code, 413)

    async def test_queue_lock_timeout_returns_http_503(self) -> None:
        """POST /chat turns stale-lock waits into retryable backpressure."""

        class _ContendedBus(_FakeBus):
            @asynccontextmanager
            async def acquire_lock(
                self,
                key: str,
                *,
                ttl_secs: int = 600,
            ) -> AsyncGenerator[None, None]:
                del ttl_secs
                if key == MessageBusKeys.chat_input_mutation_lock("s"):
                    await asyncio.Event().wait()
                yield

        with patch.object(
            MessageBusKeys,
            "CHAT_INPUT_MUTATION_TIMEOUT_SECS",
            0.01,
        ):
            with self.assertRaises(HTTPException) as raised:
                await chat_endpoint(
                    request=ChatRequest(
                        agent_id="a",
                        session_id="s",
                        input=UserMsg("user", "retry later"),
                    ),
                    user_id="u",
                    storage=_FakeStorage(),
                    chat_service=_FakeChatService(),
                    chat_run_registry=ChatRunRegistry(),
                    message_bus=_ContendedBus(),
                )
        self.assertEqual(raised.exception.status_code, 503)


class TestEditableChatQueue(IsolatedAsyncioTestCase):
    """Pending turns can be edited, deleted and reordered before start."""

    async def test_edit_delete_and_reorder_pending_turns(self) -> None:
        """All pending-item mutations preserve a consistent FIFO snapshot."""
        bus = _FakeBus()
        first = UserMsg("user", "first", id="q1")
        second = UserMsg("user", "second", id="q2")
        third = UserMsg("user", "third", id="q3")

        first_item = await enqueue_chat_input(bus, "u", "s", "a", first)
        second_item = await enqueue_chat_input(bus, "u", "s", "a", second)
        third_item = await enqueue_chat_input(bus, "u", "s", "a", third)

        edited = UserMsg("user", "second edited", id="q2")
        items = await update_chat_input(
            bus,
            "u",
            "s",
            "a",
            second_item["id"],
            edited,
        )
        self.assertEqual(
            items[1]["input"]["content"][0]["text"],
            "second edited",
        )

        items = await delete_chat_input(
            bus,
            "u",
            "s",
            "a",
            first_item["id"],
        )
        self.assertEqual(
            [item["id"] for item in items],
            [second_item["id"], third_item["id"]],
        )

        items = await reorder_chat_inputs(
            bus,
            "u",
            "s",
            "a",
            [third_item["id"], second_item["id"]],
        )
        self.assertEqual(
            [item["id"] for item in items],
            [third_item["id"], second_item["id"]],
        )
        self.assertEqual(
            [item["id"] for item in await list_chat_inputs(bus, "s")],
            [third_item["id"], second_item["id"]],
        )

    async def test_list_empty_queue_does_not_mutate_pending_registry(
        self,
    ) -> None:
        """The GET-style list operation is read-only."""
        bus = _FakeBus()
        await bus.registry_set(
            MessageBusKeys.chat_input_pending_registry(),
            "orphan-marker",
            '{"user_id": "u", "agent_id": "a"}',
        )

        self.assertEqual(await list_chat_inputs(bus, "orphan-marker"), [])
        self.assertTrue(
            await bus.registry_exists(
                MessageBusKeys.chat_input_pending_registry(),
                "orphan-marker",
            ),
        )

    async def test_started_turn_can_no_longer_be_changed(self) -> None:
        """An item disappears from management APIs once it is claimed."""
        bus = _FakeBus()
        item = await enqueue_chat_input(
            bus,
            "u",
            "s",
            "a",
            UserMsg("user", "starting", id="started"),
        )
        await bus.queue_drain(
            MessageBusKeys.chat_inputs("s"),
            max_count=1,
        )

        with self.assertRaises(LookupError):
            await delete_chat_input(bus, "u", "s", "a", item["id"])

    async def test_reorder_rejects_stale_snapshot(self) -> None:
        """Reorder requires an exact permutation of the latest snapshot."""
        bus = _FakeBus()
        first_item = await enqueue_chat_input(
            bus,
            "u",
            "s",
            "a",
            UserMsg("user", "one", id="one"),
        )
        await enqueue_chat_input(
            bus,
            "u",
            "s",
            "a",
            UserMsg("user", "two", id="two"),
        )

        with self.assertRaises(ValueError):
            await reorder_chat_inputs(
                bus,
                "u",
                "s",
                "a",
                [first_item["id"]],
            )

    async def test_duplicate_message_ids_have_distinct_queue_ids(
        self,
    ) -> None:
        """Retries of one Msg.id remain independently reorderable."""
        bus = _FakeBus()
        msg = UserMsg("user", "retry me", id="same-message")
        first = await enqueue_chat_input(bus, "u", "s", "a", msg)
        second = await enqueue_chat_input(bus, "u", "s", "a", msg)

        self.assertNotEqual(first["id"], second["id"])
        items = await reorder_chat_inputs(
            bus,
            "u",
            "s",
            "a",
            [second["id"], first["id"]],
        )
        self.assertEqual(
            [item["id"] for item in items],
            [second["id"], first["id"]],
        )

    async def test_reorder_preserves_non_owned_payload_positions(self) -> None:
        """Defensive reorder never erases a foreign/corrupt queue item."""
        bus = _FakeBus()
        first = await enqueue_chat_input(
            bus,
            "u",
            "s",
            "a",
            UserMsg("user", "first"),
        )
        foreign = await enqueue_chat_input(
            bus,
            "other",
            "s",
            "a",
            UserMsg("user", "foreign"),
        )
        second = await enqueue_chat_input(
            bus,
            "u",
            "s",
            "a",
            UserMsg("user", "second"),
        )

        await reorder_chat_inputs(
            bus,
            "u",
            "s",
            "a",
            [second["id"], first["id"]],
        )
        items = await list_chat_inputs(bus, "s")
        self.assertEqual(
            [item["id"] for item in items],
            [second["id"], foreign["id"], first["id"]],
        )

    async def test_enqueue_rejects_queue_over_capacity(self) -> None:
        """A bounded pending queue cannot grow without limit."""
        bus = _FakeBus()
        with patch.object(MessageBusKeys, "CHAT_INPUT_MAX_LEN", 2):
            await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("user", "one"),
            )
            await enqueue_chat_input(
                bus,
                "u",
                "s",
                "a",
                UserMsg("user", "two"),
            )
            with self.assertRaises(ChatQueueFullError):
                await enqueue_chat_input(
                    bus,
                    "u",
                    "s",
                    "a",
                    UserMsg("user", "three"),
                )

    async def test_enqueue_enforces_user_quota_across_sessions(self) -> None:
        """One user cannot multiply the queue cap with many sessions."""
        bus = _FakeBus()
        with patch.object(MessageBusKeys, "CHAT_INPUT_USER_MAX_LEN", 2):
            await enqueue_chat_input(
                bus,
                "u",
                "s1",
                "a",
                UserMsg("user", "one"),
            )
            await enqueue_chat_input(
                bus,
                "u",
                "s2",
                "a",
                UserMsg("user", "two"),
            )
            with self.assertRaises(ChatQueueFullError):
                await enqueue_chat_input(
                    bus,
                    "u",
                    "s3",
                    "a",
                    UserMsg("user", "three"),
                )

    async def test_enqueue_rejects_oversized_payload(self) -> None:
        """Serialized input bytes are bounded before touching the queue."""
        bus = _FakeBus()
        with patch.object(MessageBusKeys, "CHAT_INPUT_MAX_BYTES", 10):
            with self.assertRaises(ChatQueuePayloadTooLargeError):
                await enqueue_chat_input(
                    bus,
                    "u",
                    "s",
                    "a",
                    UserMsg("user", "too large"),
                )
        self.assertEqual(
            await bus.queue_read(MessageBusKeys.chat_inputs("s"), max_count=1),
            [],
        )

    async def test_mutation_lock_wait_is_bounded(self) -> None:
        """A stale short lock surfaces retryable busy instead of hanging."""

        class _ContendedBus(_FakeBus):
            @asynccontextmanager
            async def acquire_lock(
                self,
                key: str,
                *,
                ttl_secs: int = 600,
            ) -> AsyncGenerator[None, None]:
                del ttl_secs
                if key == MessageBusKeys.chat_input_mutation_lock("s"):
                    await asyncio.Event().wait()
                yield

        with patch.object(
            MessageBusKeys,
            "CHAT_INPUT_MUTATION_TIMEOUT_SECS",
            0.01,
        ):
            with self.assertRaises(ChatQueueBusyError):
                await list_chat_inputs(_ContendedBus(), "s")

    async def test_queue_snapshots_are_not_written_to_replay_log(
        self,
    ) -> None:
        """Queue mutations fan out live; GET remains the durable source."""
        bus = _FakeBus()
        await enqueue_chat_input(
            bus,
            "u",
            "s",
            "a",
            UserMsg("user", "pending"),
        )
        self.assertEqual(bus.log_appends, [])
