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
from unittest.mock import patch

import fakeredis.aioredis
from fastapi.testclient import TestClient

from utils import AnyString, FakeWorkspaceManager

from agentscope.app import create_app
from agentscope.app._manager import SchedulerManager
from agentscope.app._manager._scheduler._tools import ScheduleDelete
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import (
    ChatModelConfig,
    RedisStorage,
    ScheduleData,
    ScheduleRecord,
    ScheduleOrigin,
)
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.permission import PermissionMode
from agentscope.message import ToolResultState


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
            session.origin,
            ScheduleOrigin(schedule_id=record.id),
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

    async def _race_stale_and_fresh_reconcile(
        self,
        change: str,
    ) -> ScheduleRecord:
        """Pause the first read, apply *change*, then start a second run.

        The first reconcile captures the old records and waits; the second
        one sees the new storage state. The returned record is the one
        storage now holds.
        """
        record = _make_record()
        await self.storage.upsert_schedule(record.user_id, record)
        # ``_add_job`` needs a started scheduler to compute next_run_time.
        self.manager._scheduler.start(paused=True)
        self.addCleanup(
            self.manager._scheduler.shutdown,
            wait=False,
        )
        self.manager._add_job(record)

        original_list = self.storage.list_all_schedules
        first_read_captured = asyncio.Event()
        release_first_read = asyncio.Event()
        second_read_returned = asyncio.Event()
        calls = 0

        async def controlled_list() -> list[ScheduleRecord]:
            nonlocal calls
            calls += 1
            snapshot = await original_list()
            if calls == 1:
                first_read_captured.set()
                await release_first_read.wait()
            else:
                second_read_returned.set()
            return snapshot

        with patch.object(
            self.storage,
            "list_all_schedules",
            controlled_list,
        ):
            stale = asyncio.create_task(self.manager.reconcile())
            await first_read_captured.wait()

            if change == "disable":
                record.data.enabled = False
            else:
                record.data.cron_expression = "30 3 * * *"
            record.updated_at = datetime(2025, 6, 1)
            await self.storage.upsert_schedule(record.user_id, record)

            fresh = asyncio.create_task(self.manager.reconcile())
            try:
                # A serialised reconcile blocks the second read behind the
                # first one, so this wait times out. Without that ordering
                # the second read returns and applies immediately.
                await asyncio.wait_for(
                    second_read_returned.wait(),
                    timeout=0.2,
                )
            except asyncio.TimeoutError:
                pass
            else:
                # Let the fresh reconcile apply the new snapshot before the
                # stale one resumes.
                await asyncio.wait({fresh}, timeout=0.2)
            finally:
                release_first_read.set()
                await asyncio.gather(stale, fresh)

        return record

    async def test_a_stale_reconcile_cannot_restore_a_disabled_job(
        self,
    ) -> None:
        """A slow read that captured an enabled record must not re-add it."""
        await self._race_stale_and_fresh_reconcile("disable")

        self.assertListEqual(self.job_ids(), [])

    async def test_a_stale_reconcile_cannot_rewrite_an_edited_job(
        self,
    ) -> None:
        """A slow read must not restore the previous cron configuration."""
        record = await self._race_stale_and_fresh_reconcile("edit")

        self.assertListEqual(self.job_ids(), [record.id])
        self.assertEqual(
            self.manager._versions[record.id],
            record.updated_at,
        )


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


class TestScheduleDeleteNotification(_SchedulerOwnershipTestBase):
    """Agent-facing deletes notify the node that owns the timers."""

    async def asyncSetUp(self) -> None:
        """Create a writer that does not own the timers."""
        await super().asyncSetUp()
        self.writer = SchedulerManager(
            storage=self.storage,
            message_bus=self.bus,
            workspace_manager=FakeWorkspaceManager(),
            enabled=False,
        )
        self.delete_tool = await self._delete_tool(self.writer)
        self.owner_delete_tool = await self._delete_tool(self.manager)

    async def _delete_tool(self, manager: SchedulerManager) -> ScheduleDelete:
        """Get the delete tool for user u on the given node."""
        return next(
            tool
            for tool in await manager.list_tools(
                user_id="u",
                agent_id="a",
                chat_model_config=_make_record().data.chat_model_config,
            )
            if isinstance(tool, ScheduleDelete)
        )

    async def test_non_owner_delete_removes_owner_job(self) -> None:
        """Subscription removes the owner's job before the periodic pass."""
        record = _make_record()
        await self.storage.upsert_schedule("u", record)
        async with self.writer, self.manager:
            self.assertListEqual(self.job_ids(self.writer), [])
            self.assertListEqual(self.job_ids(), [record.id])

            result = await self.delete_tool(schedule_id=record.id)

            self.assertEqual(result.state, ToolResultState.SUCCESS)
            self.assertIsNone(await self.storage.get_schedule("u", record.id))
            for _ in range(50):
                if not self.job_ids():
                    break
                await asyncio.sleep(0.02)
            self.assertListEqual(self.job_ids(), [])

    async def test_cross_user_delete_preserves_owner_job(self) -> None:
        """A failed user-scoped delete must not remove another user's timer."""
        record = _make_record(user_id="owner")
        await self.storage.upsert_schedule("owner", record)

        async with self.manager:
            self.assertListEqual(self.job_ids(), [record.id])
            with patch.object(
                self.bus,
                "publish",
                wraps=self.bus.publish,
            ) as publish:
                result = await self.owner_delete_tool(schedule_id=record.id)

            self.assertEqual(result.state, ToolResultState.ERROR)
            self.assertIsNotNone(
                await self.storage.get_schedule("owner", record.id),
            )
            self.assertListEqual(self.job_ids(), [record.id])
            publish.assert_not_awaited()

    async def test_other_jobs_reconcile_during_delete(self) -> None:
        """B can start and stop while A is still being deleted."""
        a = _make_record()
        b = _make_record()
        await self.storage.upsert_schedule("u", a)
        delete = self.storage.delete_schedule

        async def during_delete(user_id: str, schedule_id: str) -> bool:
            if schedule_id == a.id:
                self.assertEqual(self.job_ids(), [])
                await self.storage.upsert_schedule("u", b)
                await asyncio.wait_for(self.manager.reconcile(), timeout=2)
                self.assertEqual(self.job_ids(), [b.id])
                result = await asyncio.wait_for(
                    self.owner_delete_tool(schedule_id=b.id),
                    timeout=2,
                )
                self.assertEqual(result.state, ToolResultState.SUCCESS)
                self.assertEqual(self.job_ids(), [])
            return await delete(user_id, schedule_id)

        async with self.manager:
            with patch.object(self.storage, "delete_schedule", during_delete):
                result = await self.owner_delete_tool(schedule_id=a.id)
            self.assertEqual(result.state, ToolResultState.SUCCESS)
            self.assertIsNone(await self.storage.get_schedule("u", a.id))

    async def test_concurrent_deletes_share_one_cascade(self) -> None:
        """A second delete checks storage again after the first finishes."""
        record = _make_record()
        await self.storage.upsert_schedule("u", record)
        entered = asyncio.Event()
        release = asyncio.Event()
        delete = self.storage.delete_schedule

        async def delayed_delete(user_id: str, schedule_id: str) -> bool:
            entered.set()
            await release.wait()
            return await delete(user_id, schedule_id)

        async with self.manager:
            with patch.object(
                self.storage,
                "delete_schedule",
                side_effect=delayed_delete,
            ) as cascade:
                first = asyncio.create_task(
                    self.owner_delete_tool(schedule_id=record.id),
                )
                try:
                    await asyncio.wait_for(entered.wait(), timeout=2)
                    second = asyncio.create_task(
                        self.owner_delete_tool(schedule_id=record.id),
                    )
                    await asyncio.sleep(0)
                finally:
                    release.set()
                    await first
                results = await asyncio.wait_for(
                    asyncio.gather(first, second),
                    timeout=2,
                )

            self.assertEqual(
                [r.state for r in results],
                [ToolResultState.SUCCESS, ToolResultState.ERROR],
            )
            cascade.assert_awaited_once_with("u", record.id)

    async def test_stale_read_cannot_restore_a_deleted_job(self) -> None:
        """A reconcile that read A during its cascade must still skip A."""
        record = _make_record()
        await self.storage.upsert_schedule("u", record)
        captured = asyncio.Event()
        release = asyncio.Event()
        list_all = self.storage.list_all_schedules
        delete = self.storage.delete_schedule
        reconciles = []

        async def slow_read() -> list[ScheduleRecord]:
            snapshot = await list_all()
            captured.set()
            await release.wait()
            return snapshot

        async def during_delete(user_id: str, schedule_id: str) -> bool:
            reconciles.append(asyncio.create_task(self.manager.reconcile()))
            await asyncio.wait_for(captured.wait(), timeout=2)
            return await delete(user_id, schedule_id)

        async with self.manager:
            with (
                patch.object(self.storage, "list_all_schedules", slow_read),
                patch.object(self.storage, "delete_schedule", during_delete),
                patch.object(self.manager, "notify_changed"),
            ):
                try:
                    result = await asyncio.wait_for(
                        self.owner_delete_tool(schedule_id=record.id),
                        timeout=3,
                    )
                finally:
                    release.set()
                    await asyncio.gather(*reconciles)
            self.assertEqual(result.state, ToolResultState.SUCCESS)
            self.assertEqual(self.job_ids(), [])

    async def test_cancelled_delete_restores_job_and_allows_retry(
        self,
    ) -> None:
        """Cancelling the cascade releases the schedule for a later delete."""
        record = _make_record()
        await self.storage.upsert_schedule("u", record)
        entered = asyncio.Event()

        async def wait_for_cancel(user_id: str, schedule_id: str) -> bool:
            self.assertEqual((user_id, schedule_id), ("u", record.id))
            entered.set()
            await asyncio.Event().wait()
            self.fail("The delete should have been cancelled")

        async with self.manager:
            with patch.object(
                self.storage,
                "delete_schedule",
                wait_for_cancel,
            ):
                task = asyncio.create_task(
                    self.owner_delete_tool(schedule_id=record.id),
                )
                try:
                    await asyncio.wait_for(entered.wait(), timeout=2)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

            self.assertEqual(self.job_ids(), [record.id])
            result = await asyncio.wait_for(
                self.owner_delete_tool(schedule_id=record.id),
                timeout=2,
            )
            self.assertEqual(result.state, ToolResultState.SUCCESS)
            self.assertEqual(self.job_ids(), [])

    async def test_failed_delete_restores_enabled_job(self) -> None:
        """A storage error after stopping the timer must restore it."""
        record = _make_record()
        await self.storage.upsert_schedule("u", record)

        async def fail_delete(user_id: str, schedule_id: str) -> bool:
            self.assertEqual((user_id, schedule_id), ("u", record.id))
            self.assertEqual(self.job_ids(), [])
            raise RuntimeError("storage unavailable")

        async with self.manager:
            with patch.object(self.storage, "delete_schedule", fail_delete):
                with self.assertRaisesRegex(RuntimeError, "storage"):
                    await self.owner_delete_tool(schedule_id=record.id)

            self.assertEqual(self.job_ids(), [record.id])
            self.assertEqual(
                self.manager._versions,
                {record.id: record.updated_at},
            )

    async def test_recovery_failure_preserves_delete_error(self) -> None:
        """A later reconcile can restore the job if recovery also fails."""
        record = _make_record()
        await self.storage.upsert_schedule("u", record)
        async with self.manager:
            with (
                patch.object(
                    self.storage,
                    "delete_schedule",
                    side_effect=OSError("delete failed"),
                ),
                patch.object(
                    self.manager,
                    "reconcile",
                    side_effect=RuntimeError("recovery failed"),
                ),
                self.assertRaisesRegex(OSError, "delete failed"),
            ):
                await self.owner_delete_tool(schedule_id=record.id)

            await self.manager.reconcile()
            self.assertEqual(self.job_ids(), [record.id])

    async def test_missing_delete_does_not_notify(self) -> None:
        """A missing record returns an error without a lifecycle event."""
        with patch.object(
            self.bus,
            "publish",
            wraps=self.bus.publish,
        ) as publish:
            result = await self.delete_tool(schedule_id="missing")

        self.assertEqual(result.state, ToolResultState.ERROR)
        publish.assert_not_awaited()


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
