# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for :meth:`SchedulerManager._build_trigger`.

We don't drive APScheduler here — we ask the manager to build a trigger
coroutine for a record and invoke it directly. The trigger's contract is:

- when ``ScheduleData.enabled`` is False → no side effects;
- when enabled → resolve / create a target session, push a
  ``<scheduled-task>``-wrapped :class:`HintBlock` to the session inbox,
  and enqueue one wakeup pointing at that session.

In stateful mode the session id is deterministic (``{record_id}_stateful``)
and reused across fires; in non-stateful mode a fresh session id is
created every fire.
"""
import json
from contextlib import AsyncExitStack
from datetime import datetime
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis

from utils import AnyString

from agentscope.app._manager import SchedulerManager
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import (
    ChatModelConfig,
    RedisStorage,
    ScheduleData,
    ScheduleRecord,
    SessionSource,
)
from agentscope.permission import PermissionMode


def _make_storage(
    fr: fakeredis.aioredis.FakeRedis,
) -> RedisStorage:
    """Construct a :class:`RedisStorage` bound to *fr*."""

    class _S(RedisStorage):
        async def __aenter__(self) -> "RedisStorage":  # type: ignore[override]
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _S()


def _make_bus(
    fr: fakeredis.aioredis.FakeRedis,
) -> RedisMessageBus:
    """Construct a :class:`RedisMessageBus` bound to *fr*."""

    class _B(RedisMessageBus):
        async def __aenter__(  # type: ignore[override]
            self,
        ) -> "RedisMessageBus":
            self._client = fr
            return self

        async def aclose(self) -> None:
            self._client = None

    return _B()


def _make_record(
    *,
    user_id: str = "u",
    agent_id: str = "a",
    enabled: bool = True,
    stateful: bool = False,
    description: str = "run nightly summary",
) -> ScheduleRecord:
    """Build a minimal :class:`ScheduleRecord` for the trigger test."""
    return ScheduleRecord(
        user_id=user_id,
        agent_id=agent_id,
        data=ScheduleData(
            name="sched-a",
            description=description,
            enabled=enabled,
            cron_expression="0 0 * * *",
            started_at=datetime(2025, 1, 1),
            chat_model_config=ChatModelConfig(
                type="dashscope_credential",
                credential_id="c",
                model="m",
                parameters={},
            ),
            stateful=stateful,
            permission_mode=PermissionMode.DONT_ASK,
        ),
    )


class _SchedulerFireTestBase(IsolatedAsyncioTestCase):
    """Shared fakeredis + storage + bus + manager fixture."""

    async def asyncSetUp(self) -> None:
        self.fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self._stack = AsyncExitStack()
        self.storage = await self._stack.enter_async_context(
            _make_storage(self.fr),
        )
        self.bus = await self._stack.enter_async_context(_make_bus(self.fr))
        # Do NOT enter the SchedulerManager context — that would start
        # APScheduler. We only need ``_build_trigger`` from the
        # un-started manager.
        self.manager = SchedulerManager(
            storage=self.storage,
            message_bus=self.bus,
        )

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()


class TestSchedulerFireDelivery(_SchedulerFireTestBase):
    """A fire delivers the prompt as a HintBlock + wakeup."""

    async def test_fire_pushes_hint_and_wakeup(self) -> None:
        """A fire creates a session, pushes the wrapped HintBlock to its
        inbox, and enqueues one wakeup pointing at that session."""
        record = _make_record(description="please summarise the news")
        trigger = self.manager._build_trigger(record)
        await trigger()

        # A session was created.
        sessions = await self.storage.list_sessions(
            record.user_id,
            record.agent_id,
        )
        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(
            {
                "source": session.source,
                "source_schedule_id": session.source_schedule_id,
            },
            {
                "source": SessionSource.SCHEDULE,
                "source_schedule_id": record.id,
            },
        )

        # Inbox has the wrapped HintBlock.
        inbox = await self.bus.inbox_drain(session.id, max_count=10)
        self.assertEqual(len(inbox), 1)
        hint = inbox[0][1]
        self.assertDictEqual(
            hint,
            {
                "type": "hint",
                "id": AnyString(),
                "hint": AnyString(),
                "source": json.dumps(
                    {"label": "schedule", "sublabel": record.data.name},
                ),
            },
        )
        self.assertIn("<scheduled-task>", hint["hint"])
        self.assertIn("please summarise the news", hint["hint"])

        # A wakeup is enqueued for that session.
        wakeups = await self.bus.dequeue_wakeups(max_count=10)
        self.assertEqual(len(wakeups), 1)
        self.assertEqual(
            wakeups[0],
            {
                "session_id": session.id,
                "agent_id": record.agent_id,
                "user_id": record.user_id,
                "kind": "wake",
                "input": None,
            },
        )


class TestSchedulerFireDisabled(_SchedulerFireTestBase):
    """Disabled schedules are a no-op."""

    async def test_disabled_fire_does_nothing(self) -> None:
        """A fire on a disabled schedule creates no session and no wakeup."""
        record = _make_record(enabled=False)
        trigger = self.manager._build_trigger(record)
        await trigger()

        # No session created, no wakeup enqueued.
        sessions = await self.storage.list_sessions(
            record.user_id,
            record.agent_id,
        )
        self.assertEqual(sessions, [])
        wakeups = await self.bus.dequeue_wakeups(max_count=10)
        self.assertEqual(wakeups, [])


class TestSchedulerFireStatefulMode(_SchedulerFireTestBase):
    """Stateful schedules reuse the same session id across fires."""

    async def test_stateful_fires_share_one_session(self) -> None:
        """Two fires of a stateful schedule reuse the same session id."""
        record = _make_record(stateful=True)
        trigger = self.manager._build_trigger(record)
        await trigger()
        await trigger()

        sessions = await self.storage.list_sessions(
            record.user_id,
            record.agent_id,
        )
        # Exactly ONE session reused.
        self.assertEqual(len(sessions), 1)
        self.assertEqual([s.id for s in sessions], [f"{record.id}_stateful"])

        # That single session has two HintBlocks in its inbox.
        inbox = await self.bus.inbox_drain(sessions[0].id, max_count=10)
        self.assertEqual(len(inbox), 2)

        # Two wakeups, both pointing at the same session.
        wakeups = await self.bus.dequeue_wakeups(max_count=10)
        self.assertEqual(
            wakeups,
            [
                {
                    "session_id": sessions[0].id,
                    "agent_id": record.agent_id,
                    "user_id": record.user_id,
                    "kind": "wake",
                    "input": None,
                },
                {
                    "session_id": sessions[0].id,
                    "agent_id": record.agent_id,
                    "user_id": record.user_id,
                    "kind": "wake",
                    "input": None,
                },
            ],
        )


class TestSchedulerFireNonStatefulMode(_SchedulerFireTestBase):
    """Non-stateful schedules create a fresh session every fire."""

    async def test_non_stateful_fires_create_distinct_sessions(
        self,
    ) -> None:
        """Two fires of a non-stateful schedule create distinct sessions."""
        record = _make_record(stateful=False)
        trigger = self.manager._build_trigger(record)
        await trigger()
        await trigger()

        sessions = await self.storage.list_sessions(
            record.user_id,
            record.agent_id,
        )
        self.assertEqual(len(sessions), 2)
        self.assertNotEqual(sessions[0].id, sessions[1].id)
