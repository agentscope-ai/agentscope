# -*- coding: utf-8 -*-
"""Tests for reliable resume consumer-group dispatch."""

# The boundary tests intentionally replace and call private service hooks.
# pylint: disable=protected-access

import asyncio
from unittest import IsolatedAsyncioTestCase

from agentscope.app._bus_ops import enqueue_run_trigger
from agentscope.app._manager import ChatRunRegistry, ResumeDispatcher
from agentscope.app._service import ChatService
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.event import UserInterruptEvent


class _Storage:
    """Minimal session storage stub used by dispatcher tests."""

    async def get_session(
        self,
        _user_id: str,
        _agent_id: str,
        _session_id: str,
    ) -> object:
        """Return a sentinel representing an existing session."""
        return object()


class _ChatService:
    """Record reliable resume calls without assembling a real agent."""

    def __init__(self, delay: float = 0) -> None:
        self.delay = delay
        self.calls: list[str] = []
        self.called = asyncio.Event()

    async def run_reliable_resume(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: UserInterruptEvent,
    ) -> bool:
        """Record one dispatch and optionally emulate a long-running run."""
        _ = user_id, session_id, agent_id
        self.calls.append(input_msg.id)
        self.called.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        return True


class _ConcurrentChatService:
    """Hold resume calls open so dispatcher concurrency is observable."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self.both_started = asyncio.Event()
        self.third_started = asyncio.Event()
        self.release = asyncio.Event()

    async def run_reliable_resume(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: UserInterruptEvent,
    ) -> bool:
        """Wait until the test releases all concurrently started calls."""
        _ = user_id, agent_id, input_msg
        self.calls.append(session_id)
        if len(self.calls) >= 3:
            self.third_started.set()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active >= 2:
            self.both_started.set()
        try:
            await self.release.wait()
        finally:
            self.active -= 1
        return True


class ResumeDispatcherTest(IsolatedAsyncioTestCase):
    """Exercise dispatcher assignment across competing consumers."""

    async def test_two_consumers_dispatch_one_entry_once(self) -> None:
        """Two dispatchers apply a single queued resume only once."""
        bus = InMemoryMessageBus()
        chat = _ChatService()
        event = UserInterruptEvent(reply_id="reply-1")
        async with (
            ResumeDispatcher(
                bus,
                _Storage(),
                chat,
                ChatRunRegistry(),
                consumer_name="c1",
                claim_idle_ms=100,
                read_block_ms=10,
            ),
            ResumeDispatcher(
                bus,
                _Storage(),
                chat,
                ChatRunRegistry(),
                consumer_name="c2",
                claim_idle_ms=100,
                read_block_ms=10,
            ),
        ):
            await enqueue_run_trigger(
                bus,
                user_id="u",
                session_id="s",
                agent_id="a",
                kind=MessageBusKeys.WAKEUP_KIND_RESUME,
                inputs=event,
            )
            await asyncio.wait_for(chat.called.wait(), timeout=1)
            await asyncio.sleep(0.05)

        self.assertEqual(chat.calls, [event.id])

    async def test_different_sessions_run_concurrently_within_bound(
        self,
    ) -> None:
        """One slow resume does not block another tenant's session."""
        bus = InMemoryMessageBus()
        chat = _ConcurrentChatService()
        async with ResumeDispatcher(
            bus,
            _Storage(),
            chat,
            ChatRunRegistry(),
            consumer_name="concurrent",
            read_block_ms=10,
            max_concurrency=2,
        ):
            for session_id in ("session-1", "session-2", "session-3"):
                await enqueue_run_trigger(
                    bus,
                    user_id=f"user-{session_id}",
                    session_id=session_id,
                    agent_id="agent",
                    kind=MessageBusKeys.WAKEUP_KIND_RESUME,
                    inputs=UserInterruptEvent(reply_id="reply"),
                )
            await asyncio.wait_for(chat.both_started.wait(), timeout=1)
            await asyncio.sleep(0.05)
            self.assertEqual(chat.max_active, 2)
            self.assertEqual(len(chat.calls), 2)
            chat.release.set()
            await asyncio.wait_for(chat.third_started.wait(), timeout=1)

        self.assertCountEqual(
            chat.calls,
            ["session-1", "session-2", "session-3"],
        )


class ReliableResumeBoundaryTest(IsolatedAsyncioTestCase):
    """Exercise locking, identity, reclaim, and heartbeat boundaries."""

    async def test_chat_service_enters_lock_before_loading_resume_state(
        self,
    ) -> None:
        """Reliable resume acquires the session lock before implementation."""
        bus = InMemoryMessageBus()
        service = ChatService.__new__(ChatService)
        service._message_bus = bus
        observed_locked = False

        async def _run_impl(
            _user_id: str,
            session_id: str,
            _agent_id: str,
            _input_msg: UserInterruptEvent,
            *,
            session_lock_held: bool = False,
        ) -> bool:
            nonlocal observed_locked
            observed_locked = await bus.is_locked(
                MessageBusKeys.session_lock(session_id),
            )
            return session_lock_held

        service._run_impl = _run_impl
        durable = await service.run_reliable_resume(
            "u",
            "s",
            "a",
            UserInterruptEvent(reply_id="reply-1"),
        )
        self.assertTrue(durable)
        self.assertTrue(observed_locked)

    def test_resume_identity_uses_event_id_and_payload_hash(self) -> None:
        """The durable identity distinguishes event target and payload."""
        event = UserInterruptEvent(id="event-1", reply_id="reply-1")
        same = event.model_copy(deep=True)
        different = UserInterruptEvent(id="event-1", reply_id="reply-2")

        key, payload_hash = ChatService._resume_identity(event)
        self.assertEqual(key, "reply-1:event-1")
        self.assertEqual(
            ChatService._resume_identity(same),
            (key, payload_hash),
        )
        self.assertNotEqual(
            ChatService._resume_identity(different)[1],
            payload_hash,
        )

    async def test_abandoned_pending_entry_is_reclaimed(self) -> None:
        """A dispatcher eventually handles work left by a dead consumer."""
        bus = InMemoryMessageBus()
        chat = _ChatService()
        event = UserInterruptEvent(reply_id="reply-1")
        await enqueue_run_trigger(
            bus,
            user_id="u",
            session_id="s",
            agent_id="a",
            kind=MessageBusKeys.WAKEUP_KIND_RESUME,
            inputs=event,
        )
        await bus.reliable_queue_read(
            MessageBusKeys.resume_queue(),
            MessageBusKeys.RESUME_CONSUMER_GROUP,
            "dead",
            block_ms=0,
        )
        await asyncio.sleep(0.02)

        async with ResumeDispatcher(
            bus,
            _Storage(),
            chat,
            ChatRunRegistry(),
            consumer_name="healthy",
            claim_idle_ms=10,
            read_block_ms=10,
        ):
            await asyncio.wait_for(chat.called.wait(), timeout=1)
            await asyncio.sleep(0.05)

        self.assertEqual(chat.calls, [event.id])

    async def test_heartbeat_prevents_spurious_reclaim(self) -> None:
        """Long-running healthy work stays with its original consumer."""
        bus = InMemoryMessageBus()
        chat = _ChatService(delay=0.25)
        event = UserInterruptEvent(reply_id="reply-1")
        async with (
            ResumeDispatcher(
                bus,
                _Storage(),
                chat,
                ChatRunRegistry(),
                consumer_name="slow",
                claim_idle_ms=100,
                read_block_ms=5,
            ),
            ResumeDispatcher(
                bus,
                _Storage(),
                chat,
                ChatRunRegistry(),
                consumer_name="other",
                claim_idle_ms=100,
                read_block_ms=5,
            ),
        ):
            await enqueue_run_trigger(
                bus,
                user_id="u",
                session_id="s",
                agent_id="a",
                kind=MessageBusKeys.WAKEUP_KIND_RESUME,
                inputs=event,
            )
            await asyncio.wait_for(chat.called.wait(), timeout=1)
            await asyncio.sleep(0.35)

        self.assertEqual(chat.calls, [event.id])
