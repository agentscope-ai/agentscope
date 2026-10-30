# -*- coding: utf-8 -*-
"""Tests for schedule validation before persistence and registration."""
from datetime import datetime, timedelta
from unittest import IsolatedAsyncioTestCase, TestCase
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agentscope.app._manager import SchedulerManager
from agentscope.app._manager._scheduler._tools._schedule_create import (
    ScheduleCreate,
)
from agentscope.app._router._schedule import (
    create_schedule,
    schedule_router,
    update_schedule,
)
from agentscope.app._router._schema._schedule import (
    CreateScheduleRequest,
    UpdateScheduleRequest,
)
from agentscope.app.storage import (
    ChatModelConfig,
    ScheduleData,
    ScheduleRecord,
)
from agentscope.app.deps import (
    get_current_user_id,
    get_resource_access_service,
    get_scheduler_manager,
    get_storage,
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

    async def list_schedules(self, user_id: str) -> list[ScheduleRecord]:
        """Return records persisted through the route under test."""
        return [r for r in self.upserted if r.user_id == user_id]


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


class ScheduleHttpEndTimeTest(TestCase):
    """Exercise request validation and response serialization over HTTP."""

    def setUp(self) -> None:
        """Keep real routes and schemas, replacing external services only."""
        self.storage = _Storage()
        self.scheduler = _Scheduler()
        app = FastAPI()
        app.include_router(schedule_router)
        app.dependency_overrides.update(
            {
                get_current_user_id: lambda: "user-1",
                get_storage: lambda: self.storage,
                get_resource_access_service: _Access,
                get_scheduler_manager: lambda: self.scheduler,
            },
        )
        self.client = self.enterContext(TestClient(app))

    def test_end_time_round_trips_through_create_and_list(self) -> None:
        """An offset-bearing end time survives JSON input and list output."""
        payload = {
            **_request("59 23 * * *", "Asia/Shanghai").model_dump(mode="json"),
            "ended_at": "2099-12-31T23:59:59.999+08:00",
        }
        created = self.client.post("/schedule/", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        record = self.storage.upserted[0]
        # Reuse generated metadata, but derive business fields from input.
        expected_record = {
            "id": record.id,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "user_id": "user-1",
            "agent_id": "agent-1",
            "data": {
                **{k: v for k, v in payload.items() if k != "agent_id"},
                "started_at": record.data.started_at,
                "ended_at": datetime.fromisoformat(payload["ended_at"]),
                "permission_mode": PermissionMode(payload["permission_mode"]),
                "source": "USER",
                "source_session_id": "",
            },
        }
        self.assertEqual(created.json(), {"schedule_id": record.id})
        self.assertEqual(
            [r.model_dump() for r in self.storage.upserted],
            [expected_record],
        )
        self.assertEqual(self.scheduler.notified, [record.id])
        listed = self.client.get("/schedule/")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(
            listed.json(),
            {
                "schedules": [
                    ScheduleRecord.model_validate(expected_record).model_dump(
                        mode="json",
                    ),
                ],
                "total": 1,
            },
        )
        trigger = SchedulerManager.validate_schedule(
            ScheduleRecord.model_validate(listed.json()["schedules"][0]),
        )
        last_day = datetime(2099, 12, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
        last_fire = trigger.get_next_fire_time(None, last_day)
        self.assertEqual(last_fire, last_day.replace(hour=23, minute=59))
        self.assertIsNone(
            trigger.get_next_fire_time(
                last_fire,
                last_fire + timedelta(seconds=1),
            ),
        )

    def test_invalid_end_time_returns_422_without_side_effects(self) -> None:
        """Malformed, naive, and expired end times fail before writes."""
        cases = (
            (
                "not-a-date",
                [
                    {
                        "type": "datetime_from_date_parsing",
                        "loc": ["body", "ended_at"],
                        "msg": "Input should be a valid datetime or date, "
                        "invalid character in year",
                        "input": "not-a-date",
                        "ctx": {"error": "invalid character in year"},
                    },
                ],
            ),
            (
                "2099-12-31T23:59:59",
                [
                    {
                        "type": "timezone_aware",
                        "loc": ["body", "ended_at"],
                        "msg": "Input should have timezone info",
                        "input": "2099-12-31T23:59:59",
                    },
                ],
            ),
            (
                "2000-01-01T00:00:00Z",
                "ended_at must be later than started_at",
            ),
        )
        for ended_at, expected_detail in cases:
            with self.subTest(ended_at=ended_at):
                response = self.client.post(
                    "/schedule/",
                    json={
                        **_request("0 9 * * *").model_dump(mode="json"),
                        "ended_at": ended_at,
                    },
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json(), {"detail": expected_detail})
                self.assertListEqual(self.storage.upserted, [])
                self.assertListEqual(self.scheduler.notified, [])


class ScheduleWeekdayTest(TestCase):
    """Standard cron weekdays are translated for APScheduler."""

    def test_standard_weekdays(self) -> None:
        """Numeric weekdays and names select the same calendar dates."""
        record = _record()
        start = datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC"))
        expected_days = {
            "1": 5,
            "0": 4,
            "7": 4,
            "1-5": 1,
            "0-7/3": 3,
            "mon-fri": 1,
        }
        for expression, day in expected_days.items():
            with self.subTest(expression=expression):
                record.data.cron_expression = f"0 9 * * {expression}"
                trigger = SchedulerManager.validate_schedule(record)
                self.assertEqual(
                    trigger.get_next_fire_time(None, start),
                    datetime(2026, 1, day, 9, tzinfo=ZoneInfo("UTC")),
                )

        record.data.cron_expression = "0 9 * * 8"
        with self.assertRaises(ValueError):
            SchedulerManager.validate_schedule(record)


class ScheduleCalendarTest(TestCase):
    """Standard cron combines restricted day fields with OR, not AND."""

    @staticmethod
    def _fires(record: ScheduleRecord, start: datetime, count: int) -> list:
        """Enumerate actual trigger dates, including overlap and end bounds."""
        trigger = SchedulerManager.validate_schedule(record)
        previous = None
        dates = []
        for _ in range(count):
            fire = trigger.get_next_fire_time(previous, start)
            if fire is None:
                break
            dates.append(fire)
            previous = fire
            start = fire + timedelta(microseconds=1)
        return dates

    def test_month_day_and_weekday_are_alternatives(self) -> None:
        """A monthly-plus-weekly task must not skip months of executions."""
        record = _record()
        record.data.cron_expression = "0 9 1 * 1"
        tz = ZoneInfo("UTC")
        dates = self._fires(record, datetime(2026, 10, 1, tzinfo=tz), 7)
        self.assertEqual(
            dates,
            [
                datetime(2026, month, day, 9, tzinfo=tz)
                for month, day in (
                    (10, 1),
                    (10, 5),
                    (10, 12),
                    (10, 19),
                    (10, 26),
                    (11, 1),
                    (11, 2),
                )
            ],
        )

    def test_overlapping_days_fire_once_within_activation_window(self) -> None:
        """Honor timezone and bounds without duplicate runs."""
        record = _record()
        record.data.cron_expression = "0 9 1 * mon"
        record.data.timezone = "Asia/Shanghai"
        tz = ZoneInfo(record.data.timezone)
        record.data.started_at = datetime(2026, 6, 1, 9, tzinfo=tz)
        record.data.ended_at = datetime(2026, 6, 8, 9, tzinfo=tz)
        self.assertEqual(
            self._fires(record, datetime(2026, 5, 1, tzinfo=tz), 4),
            [
                datetime(2026, 6, 1, 9, tzinfo=tz),
                datetime(2026, 6, 8, 9, tzinfo=tz),
            ],
        )

    def test_wildcard_day_fields_keep_intersection_semantics(self) -> None:
        """A wildcard, including a stepped one, does not add an OR branch."""
        tz = ZoneInfo("UTC")
        for expression, days in (
            ("0 9 * * 1", [5, 12, 19, 26]),
            ("0 9 1 * *", [1]),
            ("0 9 */2 * 1", [5, 19]),
        ):
            with self.subTest(expression=expression):
                record = _record()
                record.data.cron_expression = expression
                record.data.ended_at = datetime(2026, 10, 31, 23, tzinfo=tz)
                self.assertEqual(
                    self._fires(record, datetime(2026, 10, 1, tzinfo=tz), 6),
                    [datetime(2026, 10, day, 9, tzinfo=tz) for day in days],
                )

    def test_weekday_branch_survives_an_impossible_month_day(self) -> None:
        """February has no 31st, but the Mondays must still run."""
        record = _record()
        record.data.cron_expression = "0 9 31 2 1"
        tz = ZoneInfo("UTC")
        record.data.ended_at = datetime(2026, 2, 28, 23, tzinfo=tz)
        self.assertEqual(
            self._fires(record, datetime(2026, 2, 1, tzinfo=tz), 5),
            [datetime(2026, 2, day, 9, tzinfo=tz) for day in (2, 9, 16, 23)],
        )

    def test_invalid_weekday_is_not_hidden_by_month_day_branch(self) -> None:
        """A valid first branch must not admit an invalid schedule."""
        record = _record()
        record.data.cron_expression = "0 9 1 * notaday"
        with self.assertRaises(ValueError):
            SchedulerManager.validate_schedule(record)


# UTC-12 sits west of every real server timezone, so a start time taken
# from the server's naive wall clock lands hours in this zone's future.
_FAR_WEST_TZ = "Etc/GMT+12"


class ScheduleDefaultStartTest(IsolatedAsyncioTestCase):
    """A schedule created without a start time is active right away."""

    def _assert_fires_within_the_hour(self, record: ScheduleRecord) -> None:
        trigger = SchedulerManager.validate_schedule(record)
        now = datetime.now().astimezone()
        next_fire = trigger.get_next_fire_time(None, now)
        self.assertIsNotNone(next_fire)
        self.assertLessEqual(next_fire - now, timedelta(hours=1))

    async def test_router_default_start_is_an_absolute_instant(self) -> None:
        """An hourly schedule must not skip runs in a far-off timezone."""
        storage = _Storage()

        await create_schedule(
            _request("0 * * * *", timezone=_FAR_WEST_TZ),
            user_id="user-1",
            storage=storage,
            access=_Access(),
            scheduler=_Scheduler(),
        )

        self._assert_fires_within_the_hour(storage.upserted[0])

    async def test_tool_default_start_is_an_absolute_instant(self) -> None:
        """The agent-facing tool's default start must not drift either."""
        storage = _Storage()
        tool = ScheduleCreate(
            user_id="user-1",
            agent_id="agent-1",
            chat_model_config=_request("0 * * * *").chat_model_config,
            storage=storage,
            scheduler_manager=_Scheduler(),
        )

        await tool(
            name="hourly",
            cron_expression="0 * * * *",
            timezone=_FAR_WEST_TZ,
        )

        self._assert_fires_within_the_hour(storage.upserted[0])

    def test_model_default_start_is_an_absolute_instant(self) -> None:
        """Records built without ``started_at`` get the same guarantee."""
        record = _record()
        record.data = ScheduleData(
            name="hourly",
            cron_expression="0 * * * *",
            timezone=_FAR_WEST_TZ,
            chat_model_config=record.data.chat_model_config,
        )

        self._assert_fires_within_the_hour(record)


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

    async def test_create_without_end_time_remains_unbounded(self) -> None:
        """Omitted and explicit null end times keep the existing behavior."""
        cases: list[dict[str, None]] = [{}, {"ended_at": None}]
        for extra in cases:
            with self.subTest(extra=extra):
                storage = _Storage()
                scheduler = _Scheduler()
                payload = {
                    **_request("0 9 * * *").model_dump(
                        exclude={"ended_at"},
                    ),
                    **extra,
                }
                request = CreateScheduleRequest.model_validate(
                    payload,
                )
                self.assertEqual(
                    "ended_at" in request.model_fields_set,
                    "ended_at" in extra,
                )
                response = await create_schedule(
                    request,
                    user_id="user-1",
                    storage=storage,
                    access=_Access(),
                    scheduler=scheduler,
                )
                record = storage.upserted[0]
                self.assertEqual(
                    [r.data.model_dump() for r in storage.upserted],
                    [
                        {
                            **request.model_dump(exclude={"agent_id"}),
                            "ended_at": None,
                            "started_at": record.data.started_at,
                            "source": "USER",
                            "source_session_id": "",
                        },
                    ],
                )
                self.assertEqual(
                    response.model_dump(),
                    {"schedule_id": record.id},
                )
                self.assertEqual(scheduler.notified, [record.id])
                trigger = SchedulerManager.validate_schedule(record)
                self.assertIsNotNone(
                    trigger.get_next_fire_time(
                        None,
                        datetime(2100, 1, 1, tzinfo=ZoneInfo("UTC")),
                    ),
                )

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
