# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for :class:`SchedulerManager`'s trigger and job ownership.

We don't drive APScheduler here — we ask the manager to build a trigger
coroutine for a record and invoke it directly. The trigger's contract is:

- when ``ScheduleData.enabled`` is False → no side effects;
- when enabled → resolve / create a target session, push a
  ``<scheduled-task>``-wrapped :class:`HintBlock` to the session inbox,
  and enqueue one wakeup pointing at that session.

In stateful mode the session id is deterministic (``{record_id}_stateful``)
and reused across fires; in non-stateful mode a fresh session id is
created every fire.

The second half covers ownership: only an enabled node holds jobs, and
it learns about writes by reconciling against storage rather than by
being called in-process.
"""
import asyncio
import json
import tempfile
from contextlib import AsyncExitStack
from datetime import datetime
from unittest import IsolatedAsyncioTestCase, TestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient

from utils import AnyString, FakeWorkspaceManager

from agentscope.app import create_app
from agentscope.app._manager import SchedulerManager
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import (
    ChatModelConfig,
    RedisStorage,
    ScheduleData,
    ScheduleRecord,
    SessionSource,
)
from agentscope.app.workspace_manager import LocalWorkspaceManager
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
    channel_id: str | None = None,
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
            channel_id=channel_id,
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
            workspace_manager=FakeWorkspaceManager(),
        )

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()
        await self.fr.aclose()


class TestSchedulerFireDelivery(_SchedulerFireTestBase):
    """A fire delivers the prompt as a HintBlock + wakeup."""

    async def test_fire_pushes_hint_and_wakeup(self) -> None:
        """A fire creates a session, pushes the wrapped HintBlock to its
        inbox, and enqueues one wakeup pointing at that session."""
        record = _make_record(
            description="please summarise the news",
            channel_id="channel-1",
        )
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
                "source_channel_id": session.source_channel_id,
                "source_chat_id": session.source_chat_id,
            },
            {
                "source": SessionSource.SCHEDULE,
                "source_schedule_id": record.id,
                "source_channel_id": None,
                "source_chat_id": None,
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
                "created_at": AnyString(),
                "finished_at": AnyString(),
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


class TestSchedulerFireWorkspaceBinding(_SchedulerFireTestBase):
    """A fired session binds a workspace under the isolation policy.

    The ids below are the PER_AGENT BLAKE2b digests of ``<user>::a``.
    """

    async def test_two_users_do_not_share_one_workspace(self) -> None:
        """Two users scheduling the same agent land on distinct
        workspaces — an unbound session would pool them into one."""
        await self.manager._build_trigger(_make_record(user_id="alice"))()
        await self.manager._build_trigger(_make_record(user_id="bob"))()

        self.assertListEqual(
            [
                *(
                    s.config.workspace_id
                    for s in await self.storage.list_sessions("alice", "a")
                ),
                *(
                    s.config.workspace_id
                    for s in await self.storage.list_sessions("bob", "a")
                ),
            ],
            ["ca79105d522eba6f", "ecbe3dbe754c96ee"],
        )

    async def test_both_trigger_branches_bind_a_workspace(self) -> None:
        """The stateful and the fresh-session branch both resolve an id."""
        await self.manager._build_trigger(_make_record(stateful=True))()
        await self.manager._build_trigger(_make_record(stateful=False))()

        sessions = await self.storage.list_sessions("u", "a")
        self.assertListEqual(
            [s.config.workspace_id for s in sessions],
            ["77377d7bd2f7a1fc", "77377d7bd2f7a1fc"],
        )


class _SchedulerOwnershipTestBase(_SchedulerFireTestBase):
    """Adds job-set inspection to the shared fixture."""

    def job_ids(self, manager: SchedulerManager | None = None) -> list[str]:
        """Return the schedule ids this node currently holds timers for.

        Args:
            manager (`SchedulerManager | None`, optional):
                Which manager to inspect; defaults to the fixture's.

        Returns:
            `list[str]`: Sorted job ids.
        """
        scheduler = (manager or self.manager)._scheduler
        return sorted(job.id for job in scheduler.get_jobs())


class TestSchedulerReconcile(_SchedulerOwnershipTestBase):
    """The owner's job set follows storage, not in-process calls."""

    async def test_reconcile_holds_enabled_records_only(self) -> None:
        """A disabled record is persisted but never given a timer."""
        live = _make_record()
        await self.storage.upsert_schedule("u", live)
        await self.storage.upsert_schedule("u", _make_record(enabled=False))

        await self.manager.reconcile()

        self.assertListEqual(self.job_ids(), [live.id])

    async def test_reconcile_drops_a_deleted_record(self) -> None:
        """Deleting the record takes the timer with it."""
        record = _make_record()
        await self.storage.upsert_schedule("u", record)
        await self.manager.reconcile()

        await self.storage.delete_schedule("u", record.id)
        await self.manager.reconcile()

        self.assertListEqual(self.job_ids(), [])

    async def test_reconcile_drops_a_disabled_record(self) -> None:
        """Disabling stops the firing without deleting the record."""
        record = _make_record()
        await self.storage.upsert_schedule("u", record)
        await self.manager.reconcile()

        record.data.enabled = False
        record.updated_at = datetime(2025, 6, 1)
        await self.storage.upsert_schedule("u", record)
        await self.manager.reconcile()

        self.assertListEqual(self.job_ids(), [])

    async def test_reconcile_reregisters_an_edited_record(self) -> None:
        """A changed ``updated_at`` replaces the job; an unchanged one
        leaves it alone."""
        record = _make_record()
        await self.storage.upsert_schedule("u", record)
        await self.manager.reconcile()
        first = self.manager._scheduler.get_job(record.id)

        await self.manager.reconcile()
        self.assertIs(self.manager._scheduler.get_job(record.id), first)

        record.data.cron_expression = "30 3 * * *"
        record.updated_at = datetime(2025, 6, 1)
        await self.storage.upsert_schedule("u", record)
        await self.manager.reconcile()

        self.assertListEqual(self.job_ids(), [record.id])
        self.assertIsNot(self.manager._scheduler.get_job(record.id), first)

    async def test_reconcile_survives_an_unparseable_cron(self) -> None:
        """One bad record must not cost every schedule after it."""
        broken = _make_record()
        broken.data.cron_expression = "not a cron"
        good = _make_record()
        await self.storage.upsert_schedule("u", broken)
        await self.storage.upsert_schedule("u", good)

        await self.manager.reconcile()

        self.assertListEqual(self.job_ids(), [good.id])


class TestSchedulerOwnership(_SchedulerOwnershipTestBase):
    """Only the enabled node holds timers; every node can notify."""

    async def test_disabled_node_holds_no_timers(self) -> None:
        """Entering a disabled manager starts nothing, however many
        enabled schedules are persisted."""
        await self.storage.upsert_schedule("u", _make_record())

        disabled = SchedulerManager(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=FakeWorkspaceManager(),
            enabled=False,
        )
        async with disabled:
            self.assertListEqual(self.job_ids(disabled), [])
            self.assertFalse(disabled._scheduler.running)

    async def test_a_write_on_one_node_reaches_the_owner(self) -> None:
        """The owner picks up a schedule written by another node, having
        never been called in-process."""
        owner = SchedulerManager(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=FakeWorkspaceManager(),
        )
        async with owner:
            self.assertListEqual(self.job_ids(owner), [])

            record = _make_record()
            await self.storage.upsert_schedule("u", record)
            # ``self.manager`` stands in for an API node with no timers.
            await self.manager.notify_changed(record.id)

            for _ in range(50):
                await asyncio.sleep(0.02)
                if self.job_ids(owner):
                    break
            self.assertListEqual(self.job_ids(owner), [record.id])


class TestSchedulerFlag(TestCase):
    """``enable_scheduler`` decides whether a process owns the timers."""

    def _boot(self, enabled: bool) -> SchedulerManager:
        """Run an app's lifespan and hand back its scheduler manager.

        Args:
            enabled (`bool`): The ``enable_scheduler`` value to pass.

        Returns:
            `SchedulerManager`: The manager the lifespan built.
        """
        # pylint: disable=consider-using-with
        workdir = self.enterContext(tempfile.TemporaryDirectory())
        fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        app = create_app(
            storage=_make_storage(fr),
            message_bus=_make_bus(fr),
            workspace_manager=LocalWorkspaceManager(workdir),
            enable_index_worker=False,
            enable_scheduler=enabled,
        )
        self.enterContext(TestClient(app))
        return app.state.scheduler_manager

    def test_disabled_process_starts_no_scheduler(self) -> None:
        """The manager is still there for the API and the agent tools,
        it just runs nothing."""
        manager = self._boot(False)

        self.assertFalse(manager._scheduler.running)
        self.assertListEqual(list(manager._scheduler.get_jobs()), [])

    def test_enabled_process_starts_the_scheduler(self) -> None:
        """The default keeps a single-process deployment working."""
        self.assertTrue(self._boot(True)._scheduler.running)
