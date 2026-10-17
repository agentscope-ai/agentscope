# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for :meth:`SchedulerManager.reconcile` ordering.

Reconcile is driven from two places: the periodic loop and the lifecycle
listener. Both call the same method, so two runs can overlap. The failure
this file pins is an *ordering* one: a read that captured the old records
finishing after a newer read has already applied its changes restores a job
the newer pass deleted (or rewrites an edited job with the previous record).

The test uses a controlled storage double, real APScheduler and a paused
first read, so the interleaving is deterministic and no timer fires.
"""
import asyncio
from datetime import datetime
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase

from agentscope.app._manager import SchedulerManager
from agentscope.app.storage import (
    ChatModelConfig,
    ScheduleData,
    ScheduleRecord,
)
from agentscope.permission import PermissionMode


def _record(
    version: int,
    *,
    enabled: bool = True,
) -> ScheduleRecord:
    """Build one schedule record whose ``updated_at`` encodes *version*."""
    return ScheduleRecord(
        id="s1",
        user_id="user",
        agent_id="agent",
        updated_at=datetime(2026, 1, 1, 0, 0, version),
        data=ScheduleData(
            name=f"version-{version}",
            enabled=enabled,
            cron_expression="0 0 * * *",
            started_at=datetime(2025, 1, 1),
            chat_model_config=ChatModelConfig(
                type="dashscope_credential",
                credential_id="c",
                model="m",
                parameters={},
            ),
            permission_mode=PermissionMode.DONT_ASK,
        ),
    )


class _ControlledStorage:
    """Storage whose first read can be paused after it captured a snapshot.

    The snapshot is taken before the pause, which is what makes the stale
    read reachable: the caller is holding the *old* records while newer
    ones are already in storage.
    """

    def __init__(self, records: list[ScheduleRecord]) -> None:
        self.records = records
        self.calls = 0
        self.first_read_captured = asyncio.Event()
        self.release_first_read = asyncio.Event()

    async def list_all_schedules(self) -> list[ScheduleRecord]:
        """Return the current records, pausing the first read on request."""
        self.calls += 1
        snapshot = list(self.records)
        if self.calls == 1:
            self.first_read_captured.set()
            await self.release_first_read.wait()
        return snapshot


class _ReconcileOrderingTest(IsolatedAsyncioTestCase):
    """A stale reconcile must not undo a newer one."""

    async def asyncSetUp(self) -> None:
        self.storage = _ControlledStorage([_record(1)])
        self.manager = SchedulerManager(
            cast(Any, self.storage),
            cast(Any, None),
            cast(Any, None),
        )
        # Production reconciles only after ``__aenter__`` started the
        # scheduler; a pending job has no ``next_run_time`` yet.
        self.manager._scheduler.start()
        self.addAsyncCleanup(self._shutdown)

    async def _shutdown(self) -> None:
        self.manager._scheduler.shutdown(wait=False)

    async def test_a_stale_read_cannot_restore_a_deleted_job(self) -> None:
        """The delete wins even when the older read finishes last."""
        # The job for version 1 is already registered, as it would be on a
        # node that has been reconciling normally.
        self.manager._add_job(_record(1))
        self.assertEqual(set(self.manager._versions), {"s1"})

        # 1. Start a reconcile; it captures version 1 and pauses.
        stale = asyncio.create_task(self.manager.reconcile())
        await self.storage.first_read_captured.wait()

        # 2. The schedule is disabled in storage, and a second reconcile
        #    observes that and drops the job.
        self.storage.records = [_record(2, enabled=False)]
        fresh = asyncio.create_task(self.manager.reconcile())
        for _ in range(3):
            await asyncio.sleep(0)

        # 3. Let the stale read return and finish.
        self.storage.release_first_read.set()
        await asyncio.gather(stale, fresh)

        self.assertEqual(
            self.manager._versions,
            {},
            "the stale reconcile re-registered a schedule that storage "
            "had already disabled",
        )
        self.assertEqual(
            [job.id for job in self.manager._scheduler.get_jobs()],
            [],
        )

    async def test_a_stale_read_cannot_rewrite_an_edited_job(self) -> None:
        """The edit wins even when the older read finishes last."""
        self.manager._add_job(_record(1))

        stale = asyncio.create_task(self.manager.reconcile())
        await self.storage.first_read_captured.wait()

        self.storage.records = [_record(3)]
        fresh = asyncio.create_task(self.manager.reconcile())
        for _ in range(3):
            await asyncio.sleep(0)

        self.storage.release_first_read.set()
        await asyncio.gather(stale, fresh)

        self.assertEqual(
            self.manager._versions["s1"],
            _record(3).updated_at,
            "the stale reconcile rewrote the edited schedule with the "
            "previous version",
        )
