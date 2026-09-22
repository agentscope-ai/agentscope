# -*- coding: utf-8 -*-
"""Tests for schedule validation before persistence and registration."""
from datetime import datetime, timedelta
from unittest import IsolatedAsyncioTestCase, TestCase
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from agentscope.app._manager import SchedulerManager
from agentscope.app._manager._scheduler._tools._schedule_create import (
    ScheduleCreate,
)
from agentscope.app._router._schedule import create_schedule, update_schedule
from agentscope.app._router._schema._schedule import (
    CreateScheduleRequest,
    UpdateScheduleRequest,
)
from agentscope.app.storage import (
    ChatModelConfig,
    ScheduleData,
    ScheduleRecord,
)
from agentscope.permission import PermissionMode


class _Access:
    """Allow all resources used by the route under test."""

    async def resolve_agent(self, user_id: str, agent_id: str) -> None:
        """Accept the requested agent."""

    async def get_resource(
        self,
        user_id: str,
        kind: object,
        resource_id: str,
    ) -> None:
        """Accept the requested credential."""


class _Storage:
    """Record schedule writes without requiring Redis or SQL."""

    def __init__(self, existing: ScheduleRecord | None = None) -> None:
        self.existing = existing
        self.upserted: list[ScheduleRecord] = []

    async def get_schedule(
        self,
        user_id: str,
        schedule_id: str,
    ) -> ScheduleRecord | None:
        """Return the one fixture record."""
        _ = user_id, schedule_id
        return self.existing

    async def upsert_schedule(
        self,
        user_id: str,
        record: ScheduleRecord,
    ) -> str:
        """Record the write."""
        _ = user_id
        self.upserted.append(record)
        return record.id


class _Scheduler(SchedulerManager):
    """Track what the owner would be told, keeping real validation."""

    def __init__(self) -> None:
        super().__init__(
            storage=None,
            message_bus=None,
            workspace_manager=None,
        )
        self.notified: list[str] = []

    async def notify_changed(self, schedule_id: str) -> None:
        """Record the nudge without touching the message bus."""
        self.notified.append(schedule_id)


def _request(
    cron_expression: str,
    timezone: str = "UTC",
) -> CreateScheduleRequest:
    """Build a minimal schedule request."""
    return CreateScheduleRequest(
        name="test schedule",
        cron_expression=cron_expression,
        timezone=timezone,
        agent_id="agent-1",
        chat_model_config=ChatModelConfig(
            type="test",
            credential_id="credential-1",
            model="model-1",
            parameters={},
        ),
    )


def _record() -> ScheduleRecord:
    """Build a valid existing schedule record."""
    return ScheduleRecord(
        id="schedule-1",
        user_id="user-1",
        agent_id="agent-1",
        data=ScheduleData(
            name="existing",
            cron_expression="0 9 * * *",
            timezone="UTC",
            started_at=datetime(2026, 1, 1),
            chat_model_config=ChatModelConfig(
                type="test",
                credential_id="credential-1",
                model="model-1",
                parameters={},
            ),
            permission_mode=PermissionMode.DONT_ASK,
        ),
    )


class ScheduleWeekdayTest(TestCase):
    """The shared trigger boundary accepts standard cron weekdays."""

    def test_standard_weekdays_fire_on_expected_dates(self) -> None:
        """Compare two-week fire sequences, including Sunday aliases."""
        cases = {
            "0": [0, 7],
            "7": [0, 7],
            "1": [1, 8],
            "6": [6, 13],
            "1-5": [1, 2, 3, 4, 5, 8, 9, 10, 11, 12],
            "0,6": [0, 6, 7, 13],
            "0,7": [0, 7],
            "5-7": [0, 5, 6, 7, 12, 13],
            "*/2": [0, 2, 4, 6, 7, 9, 11, 13],
            "1-5/2": [1, 3, 5, 8, 10, 12],
            "1/2": [0, 1, 3, 5, 7, 8, 10, 12],
            "0-7/3": [0, 3, 6, 7, 10, 13],
            "*": list(range(14)),
            "*/1": list(range(14)),
            "SUN": [0, 7],
            "mon-fri": [1, 2, 3, 4, 5, 8, 9, 10, 11, 12],
            "mon-fri/2": [1, 3, 5, 8, 10, 12],
            "MoN-FrI/2": [1, 3, 5, 8, 10, 12],
            "sun-sat": list(range(14)),
            "sun-mon": [0, 1, 7, 8],
            "sun-sun": [0, 7],
            "sun-sat/2": [0, 2, 4, 6, 7, 9, 11, 13],
            "mon/2": [0, 1, 3, 5, 7, 8, 10, 12],
            "sun/2": [0, 2, 4, 6, 7, 9, 11, 13],
            "mon-sun": list(range(14)),
            "mon-sun/2": [0, 1, 3, 5, 7, 8, 10, 12],
            "sat-sun": [0, 6, 7, 13],
            "fri-sun": [0, 5, 6, 7, 12, 13],
            "sun-6/2": [0, 2, 4, 6, 7, 9, 11, 13],
            "1-fri/2": [1, 3, 5, 8, 10, 12],
            "mon,wed,fri": [1, 3, 5, 8, 10, 12],
            "1,wed,5-6": [1, 3, 5, 6, 8, 10, 12, 13],
        }
        start = datetime(2026, 9, 20, tzinfo=ZoneInfo("UTC"))
        end = start + timedelta(days=14)
        for expression, offsets in cases.items():
            with self.subTest(expression=expression):
                record = _record()
                record.data.cron_expression = f"0 9 * * {expression}"
                record.data.started_at = start
                before = record.model_dump()
                trigger = SchedulerManager.validate_schedule(record)
                actual = []
                fire = trigger.get_next_fire_time(None, start)
                while fire is not None and fire < end:
                    actual.append(fire)
                    fire = trigger.get_next_fire_time(
                        fire,
                        fire + timedelta(seconds=1),
                    )
                self.assertEqual(
                    actual,
                    [start + timedelta(days=d, hours=9) for d in offsets],
                )
                self.assertEqual(record.model_dump(), before)

    def test_weekday_normalization_preserves_timezone_and_window(self) -> None:
        """The other fields and activation window keep their semantics."""
        zone = ZoneInfo("Asia/Shanghai")
        record = _record()
        record.data.cron_expression = "15,45 9-10 * jan,sep 1"
        record.data.timezone = "Asia/Shanghai"
        record.data.started_at = datetime(2026, 9, 21, 9, 30, tzinfo=zone)
        record.data.ended_at = datetime(2026, 9, 21, 10, 30, tzinfo=zone)
        trigger = SchedulerManager.validate_schedule(record)
        actual = []
        fire = trigger.get_next_fire_time(None, record.data.started_at)
        for _ in range(3):
            actual.append(fire)
            if fire is None:
                break
            fire = trigger.get_next_fire_time(
                fire,
                fire + timedelta(seconds=1),
            )
        self.assertEqual(
            actual,
            [
                datetime(2026, 9, 21, 9, 45, tzinfo=zone),
                datetime(2026, 9, 21, 10, 15, tzinfo=zone),
                None,
            ],
        )

    def test_invalid_weekdays_are_rejected(self) -> None:
        """Normalization must not accept invalid weekday expressions."""
        for expression in (
            "8",
            "-1",
            "1-8",
            "7-1",
            "*/0",
            "1-5/0",
            "1,,2",
            "1/abc",
            "1-2-3",
            "1/2/3",
            "mon-fri/0",
            "mon/0",
            "mon-fri/abc",
            "mon!",
            "mon-fri!",
            "mon-fri/2tail",
            "sun-sat/2/3",
            "mon-fri-sat",
            "noday",
            "fri-mon",
        ):
            with self.subTest(expression=expression):
                record = _record()
                record.data.cron_expression = f"0 9 * * {expression}"
                with self.assertRaises(ValueError):
                    SchedulerManager.validate_schedule(record)


class ScheduleValidationTest(IsolatedAsyncioTestCase):
    """Invalid schedules must fail before any state mutation."""

    async def test_create_valid_schedule_persists_and_notifies(self) -> None:
        """The rejections below only mean something if this passes."""
        storage = _Storage()
        scheduler = _Scheduler()

        response = await create_schedule(
            _request("0 9 * * *"),
            user_id="user-1",
            storage=storage,
            access=_Access(),
            scheduler=scheduler,
        )

        self.assertEqual(len(storage.upserted), 1)
        self.assertEqual(storage.upserted[0].id, response.schedule_id)
        self.assertListEqual(scheduler.notified, [response.schedule_id])

    async def test_create_empty_timezone_does_not_persist(self) -> None:
        """An empty timezone must not silently mean server-local."""
        storage = _Storage()
        scheduler = _Scheduler()

        with self.assertRaises(HTTPException) as ctx:
            await create_schedule(
                _request("0 9 * * *", timezone=""),
                user_id="user-1",
                storage=storage,
                access=_Access(),
                scheduler=scheduler,
            )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(storage.upserted, [])
        self.assertEqual(scheduler.notified, [])

    async def test_create_invalid_cron_does_not_persist(self) -> None:
        """Create rejects invalid cron before writing the schedule."""
        storage = _Storage()
        scheduler = _Scheduler()

        with self.assertRaises(HTTPException) as ctx:
            await create_schedule(
                _request("not a cron"),
                user_id="user-1",
                storage=storage,
                access=_Access(),
                scheduler=scheduler,
            )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(storage.upserted, [])
        self.assertEqual(scheduler.notified, [])

    async def test_create_out_of_range_cron_does_not_persist(self) -> None:
        """Cron field ranges are checked without constructing a trigger."""
        storage = _Storage()
        scheduler = _Scheduler()

        with self.assertRaises(HTTPException) as ctx:
            await create_schedule(
                _request("61 9 * * *"),
                user_id="user-1",
                storage=storage,
                access=_Access(),
                scheduler=scheduler,
            )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(storage.upserted, [])
        self.assertEqual(scheduler.notified, [])

    async def test_create_invalid_timezone_does_not_persist(self) -> None:
        """Create rejects an unknown timezone before writing the schedule."""
        storage = _Storage()
        scheduler = _Scheduler()

        with self.assertRaises(HTTPException) as ctx:
            await create_schedule(
                _request("0 9 * * *", timezone="Mars/Olympus_Mons"),
                user_id="user-1",
                storage=storage,
                access=_Access(),
                scheduler=scheduler,
            )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(storage.upserted, [])
        self.assertEqual(scheduler.notified, [])

    async def test_update_invalid_cron_keeps_existing_state(self) -> None:
        """Update rejects invalid cron before persisting or notifying."""
        existing = _record()
        storage = _Storage(existing)
        scheduler = _Scheduler()

        with self.assertRaises(HTTPException) as ctx:
            await update_schedule(
                "schedule-1",
                UpdateScheduleRequest(cron_expression="not a cron"),
                user_id="user-1",
                storage=storage,
                scheduler=scheduler,
            )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(storage.upserted, [])
        self.assertEqual(scheduler.notified, [])

    async def test_update_invalid_timezone_keeps_existing_state(self) -> None:
        """Update rejects an unknown timezone before changing state."""
        existing = _record()
        storage = _Storage(existing)
        scheduler = _Scheduler()

        with self.assertRaises(HTTPException) as ctx:
            await update_schedule(
                "schedule-1",
                UpdateScheduleRequest(timezone="Mars/Olympus_Mons"),
                user_id="user-1",
                storage=storage,
                scheduler=scheduler,
            )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(storage.upserted, [])
        self.assertEqual(scheduler.notified, [])

    async def test_tool_invalid_cron_does_not_persist(self) -> None:
        """The agent-facing create tool validates before writing too."""
        storage = _Storage()
        scheduler = _Scheduler()
        tool = ScheduleCreate(
            user_id="user-1",
            agent_id="agent-1",
            chat_model_config=_request("0 9 * * *").chat_model_config,
            storage=storage,
            scheduler_manager=scheduler,
        )

        with self.assertRaises(ValueError):
            await tool(name="bad", cron_expression="not a cron")

        self.assertEqual(storage.upserted, [])

    async def test_tool_invalid_timezone_does_not_persist(self) -> None:
        """The agent-facing tool rejects an unknown timezone before writing."""
        storage = _Storage()
        scheduler = _Scheduler()
        tool = ScheduleCreate(
            user_id="user-1",
            agent_id="agent-1",
            chat_model_config=_request("0 9 * * *").chat_model_config,
            storage=storage,
            scheduler_manager=scheduler,
        )

        with self.assertRaises(ValueError):
            await tool(
                name="bad timezone",
                cron_expression="0 9 * * *",
                timezone="Mars/Olympus_Mons",
            )

        self.assertEqual(storage.upserted, [])

    async def test_tool_invalid_time_window_does_not_persist(self) -> None:
        """The agent-facing tool rejects a reversed activation window."""
        storage = _Storage()
        scheduler = _Scheduler()
        tool = ScheduleCreate(
            user_id="user-1",
            agent_id="agent-1",
            chat_model_config=_request("0 9 * * *").chat_model_config,
            storage=storage,
            scheduler_manager=scheduler,
        )

        with self.assertRaises(ValueError):
            await tool(
                name="bad window",
                cron_expression="0 9 * * *",
                started_at=datetime(2026, 1, 2),
                ended_at=datetime(2026, 1, 1),
            )

        self.assertEqual(storage.upserted, [])
