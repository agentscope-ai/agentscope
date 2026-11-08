# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for the session-inbox hand-off protocol in ``_bus_ops``.

The protocol exists so a payload pushed to a session inbox is always
consumed by *some* run, instead of sitting there until the next user
turn. Producers and the finishing run coordinate through one lock:

- :func:`deliver_to_inbox` pushes, then wakes the session only when no
  run is registered as its consumer.
- :func:`has_pending_inbox_or_release` lets a finishing run take one
  more look and, finding nothing, stop being that consumer.

The tests below cover both orderings of that race plus the abnormal
exit path.
"""
import asyncio
from contextlib import AsyncExitStack
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from types import SimpleNamespace

from agentscope.app._bus_ops import (
    abandon_inbox_consumer,
    deliver_to_inbox,
    has_pending_inbox_or_release,
    register_inbox_consumer,
)
from agentscope.app._service import SessionService
from agentscope.app._service._session import SessionBusyError
from agentscope.app.channel._approval import (
    ChannelApprovalRecord,
    load_approval,
    new_approval_id,
    load_reply_requester,
    remember_reply_requester,
    store_approval,
)
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys


class TestInboxHandoff(IsolatedAsyncioTestCase):
    """Producer / finishing-run hand-off around a session inbox."""

    async def asyncSetUp(self) -> None:
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(InMemoryMessageBus())
        self.sid = "sess-1"

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()

    async def _wakeups(self) -> list[dict]:
        """Drain and return the trigger queue payloads."""
        entries = await self.bus.queue_drain(MessageBusKeys.wakeup_queue())
        return [payload for _entry_id, payload in entries]

    async def _deliver(self, text: str) -> None:
        """Push one payload through the producer helper."""
        await deliver_to_inbox(
            self.bus,
            user_id="u",
            session_id=self.sid,
            agent_id="a",
            payload={"type": "hint", "hint": text},
        )

    async def test_no_consumer_pushes_and_wakes(self) -> None:
        """With nobody consuming, a delivery enqueues a wake-up."""
        await self._deliver("hello")

        self.assertEqual(len(await self._wakeups()), 1)
        entries = await self.bus.queue_drain(MessageBusKeys.inbox(self.sid))
        self.assertEqual([p["hint"] for _i, p in entries], ["hello"])

    async def test_registered_consumer_suppresses_wakeup(self) -> None:
        """A registered consumer is expected to drain it itself, so no
        wake-up is enqueued."""
        await register_inbox_consumer(self.bus, self.sid)
        await self._deliver("hello")

        self.assertEqual(await self._wakeups(), [])

    async def test_pending_payload_keeps_consumer_registered(self) -> None:
        """A run finding leftovers stays the consumer and keeps them
        queued, in arrival order, for its next turn."""
        await register_inbox_consumer(self.bus, self.sid)
        await self._deliver("first")
        await self._deliver("second")

        self.assertTrue(
            await has_pending_inbox_or_release(self.bus, self.sid),
        )
        self.assertIsNotNone(
            await self.bus.registry_get(
                MessageBusKeys.inbox_consumer(self.sid),
                MessageBusKeys.INBOX_CONSUMER_FIELD,
            ),
        )
        entries = await self.bus.queue_drain(MessageBusKeys.inbox(self.sid))
        self.assertEqual(
            [p["hint"] for _i, p in entries],
            ["first", "second"],
        )

    async def test_empty_inbox_releases_consumer(self) -> None:
        """An empty inbox releases the registration, so the next
        delivery wakes the session instead of deferring to this run."""
        await register_inbox_consumer(self.bus, self.sid)

        self.assertFalse(
            await has_pending_inbox_or_release(self.bus, self.sid),
        )
        self.assertIsNone(
            await self.bus.registry_get(
                MessageBusKeys.inbox_consumer(self.sid),
                MessageBusKeys.INBOX_CONSUMER_FIELD,
            ),
        )

        await self._deliver("after release")
        self.assertEqual(len(await self._wakeups()), 1)

    async def test_delivery_during_release_is_not_stranded(self) -> None:
        """The race this protocol exists for: a payload pushed just as a
        run finishes is either seen by that run's last look or wakes the
        session — never neither."""
        await register_inbox_consumer(self.bus, self.sid)

        release = asyncio.create_task(
            has_pending_inbox_or_release(self.bus, self.sid),
        )
        deliver = asyncio.create_task(self._deliver("racing"))
        pending, _ = await asyncio.gather(release, deliver)

        wakeups = await self._wakeups()
        # Exactly one of the two outcomes, never zero: either the run
        # keeps going (it saw the payload), or a wake-up was enqueued.
        self.assertEqual(pending, not wakeups)
        # The payload itself is still queued for whoever handles it.
        entries = await self.bus.queue_drain(MessageBusKeys.inbox(self.sid))
        self.assertEqual([p["hint"] for _i, p in entries], ["racing"])

    async def test_abandon_wakes_when_payloads_remain(self) -> None:
        """A run that dies mid-turn hands its leftovers to a fresh run."""
        await register_inbox_consumer(self.bus, self.sid)
        await self._deliver("unhandled")

        await abandon_inbox_consumer(
            self.bus,
            user_id="u",
            session_id=self.sid,
            agent_id="a",
        )

        self.assertEqual(len(await self._wakeups()), 1)
        self.assertIsNone(
            await self.bus.registry_get(
                MessageBusKeys.inbox_consumer(self.sid),
                MessageBusKeys.INBOX_CONSUMER_FIELD,
            ),
        )

    async def test_abandon_with_empty_inbox_does_not_wake(self) -> None:
        """Nothing left means nothing to hand over — and no empty run."""
        await register_inbox_consumer(self.bus, self.sid)

        await abandon_inbox_consumer(
            self.bus,
            user_id="u",
            session_id=self.sid,
            agent_id="a",
        )

        self.assertEqual(await self._wakeups(), [])

    async def test_session_purge_releases_consumer_for_reused_id(self) -> None:
        """Deleting a session must not suppress delivery to a reused ID."""
        await register_inbox_consumer(self.bus, self.sid)
        service = SessionService(storage=None, message_bus=self.bus)

        await service._purge_session_bus(self.sid)
        await self._deliver("after delete")

        self.assertEqual(len(await self._wakeups()), 1)
        entries = await self.bus.queue_drain(MessageBusKeys.inbox(self.sid))
        self.assertEqual([p["hint"] for _i, p in entries], ["after delete"])

    async def test_session_purge_removes_only_its_channel_approval_state(
        self,
    ) -> None:
        service = SessionService(storage=None, message_bus=self.bus)
        approvals = {
            session_id: new_approval_id(session_id)
            for session_id in (self.sid, "other-session")
        }
        for session_id in approvals:
            await remember_reply_requester(
                self.bus,
                session_id=session_id,
                reply_id="reply",
                requester_id="requester",
            )
            await store_approval(
                self.bus,
                approvals[session_id],
                ChannelApprovalRecord(
                    channel_id="channel",
                    chat_id="chat",
                    agent_id="agent",
                    session_id=session_id,
                    reply_id="reply",
                    tool_call_id="call",
                    requester_id="requester",
                ),
            )

        await service._purge_session_bus(self.sid)

        self.assertIsNone(await load_approval(self.bus, approvals[self.sid]))
        self.assertIsNotNone(
            await load_approval(self.bus, approvals["other-session"])
        )
        self.assertEqual(
            await load_reply_requester(
                self.bus,
                session_id=self.sid,
                reply_id="reply",
            ),
            "",
        )

    async def test_delete_lock_timeout_releases_prior_locks(self) -> None:
        storage = SimpleNamespace(
            get_session=AsyncMock(
                return_value=SimpleNamespace(
                    config=SimpleNamespace(workspace_id=None),
                ),
            ),
            delete_session=AsyncMock(return_value=True),
        )
        service = SessionService(storage=storage, message_bus=self.bus)
        service._DELETE_LOCK_WAIT_SECS = 0.02
        service._team_worker_session_ids = AsyncMock(return_value=["z-worker"])
        service.cancel_session_run = AsyncMock(return_value=False)
        service._purge_subagent_hitl = AsyncMock()
        service._purge_session_bus = AsyncMock()

        async with self.bus.acquire_lock(
            MessageBusKeys.session_lock("z-worker")
        ):
            with self.assertRaises(SessionBusyError):
                await asyncio.wait_for(
                    service.delete_session("u", "a", self.sid),
                    timeout=1.0,
                )
            self.assertFalse(
                await self.bus.is_locked(
                    MessageBusKeys.session_lock(self.sid)
                ),
            )
            self.assertTrue(
                await self.bus.is_locked(
                    MessageBusKeys.session_lock("z-worker")
                ),
            )
        storage.delete_session.assert_not_awaited()
        service._purge_subagent_hitl.assert_not_awaited()
        service._purge_session_bus.assert_not_awaited()

    async def test_delete_lock_budget_does_not_timeout_deletion(self) -> None:
        async def slow_delete(*_args: object) -> bool:
            await asyncio.sleep(0.04)
            return True

        storage = SimpleNamespace(
            get_session=AsyncMock(
                return_value=SimpleNamespace(
                    config=SimpleNamespace(workspace_id=None),
                ),
            ),
            delete_session=AsyncMock(side_effect=slow_delete),
        )
        service = SessionService(storage=storage, message_bus=self.bus)
        service._DELETE_LOCK_WAIT_SECS = 0.01
        service._team_worker_session_ids = AsyncMock(return_value=[])
        service.cancel_session_run = AsyncMock(return_value=True)
        service._purge_subagent_hitl = AsyncMock()
        service._purge_session_bus = AsyncMock()

        self.assertTrue(await service.delete_session("u", "a", self.sid))
        service._purge_session_bus.assert_awaited_once_with(self.sid)
        self.assertFalse(
            await self.bus.is_locked(MessageBusKeys.session_lock(self.sid)),
        )
