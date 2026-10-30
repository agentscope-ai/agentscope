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

    def test_start_time_round_trip_and_frequency_boundaries(self) -> None:
        """All frequencies retain their rules and inclusive start/end times."""
        cases = (
            ("daily", "26 20 * * *", "20:26", ("2026-11-01", "2026-11-02")),
            ("midnight", "15 0 * * *", "00:15", ("2030-01-01", "2030-01-02")),
            ("weekly", "26 20 * * 0", "20:26", ("2026-11-01", "2026-11-08")),
            ("monthly", "26 20 1 * *", "20:26", ("2026-11-01", "2026-12-01")),
            (
                "month-end",
                "15 0 31 * *",
                "00:15",
                ("2030-01-31", "2030-03-31"),
            ),
            ("once", "26 20 1 11 *", "20:26", ("2030-11-01",)),
            ("once-leap-day", "26 20 29 2 *", "20:26", ("2032-02-29",)),
        )
        for frequency, cron, time, dates in cases:
            expected_fires = [
                datetime.fromisoformat(f"{day}T{time}:00+08:00")
                for day in dates
            ]
            first = expected_fires[0]
            utc_start = first.astimezone(ZoneInfo("UTC")).isoformat()
            starts = (
                first.isoformat(),
                utc_start.replace("+00:00", "Z"),
            )
            for start in starts:
                for bounded in (True, False):
                    with self.subTest(
                        frequency=frequency,
                        start=start,
                        bounded=bounded,
                    ):
                        self.storage.upserted.clear()
                        self.scheduler.notified.clear()
                        end = expected_fires[-1] if bounded else None
                        if end is not None and end == first:
                            end += timedelta(days=1)
                        payload = {
                            **_request(cron, "Asia/Shanghai").model_dump(
                                mode="json",
                            ),
                            "started_at": start,
                            "ended_at": end.isoformat() if end else None,
                        }
                        created = self.client.post("/schedule/", json=payload)
                        self.assertEqual(
                            created.status_code,
                            201,
                            created.text,
                        )
                        record = self.storage.upserted[0]
                        expected_record = {
                            "id": record.id,
                            "created_at": record.created_at,
                            "updated_at": record.updated_at,
                            "user_id": "user-1",
                            "agent_id": payload["agent_id"],
                            "data": {
                                **{
                                    k: v
                                    for k, v in payload.items()
                                    if k != "agent_id"
                                },
                                "started_at": datetime.fromisoformat(start),
                                "ended_at": end,
                                "permission_mode": PermissionMode.DONT_ASK,
                                "source": "USER",
                                "source_session_id": "",
                            },
                        }
                        self.assertEqual(
                            [r.model_dump() for r in self.storage.upserted],
                            [expected_record],
                        )
                        self.assertEqual(
                            created.json(),
                            {"schedule_id": record.id},
                        )
                        self.assertEqual(self.scheduler.notified, [record.id])
                        listed = self.client.get("/schedule/")
                        self.assertEqual(listed.status_code, 200, listed.text)
                        expected = ScheduleRecord.model_validate(
                            expected_record,
                        )
                        self.assertEqual(
                            listed.json(),
                            {
                                "schedules": [
                                    expected.model_dump(mode="json"),
                                ],
                                "total": 1,
                            },
                        )
                        restored = ScheduleRecord.model_validate(
                            listed.json()["schedules"][0],
                        )
                        trigger = SchedulerManager.validate_schedule(restored)
                        self.assertEqual(
                            [
                                trigger.get_next_fire_time(None, reference)
                                for reference in (
                                    first - timedelta(days=2000),
                                    first - timedelta(microseconds=1),
                                    first,
                                )
                            ],
                            [first] * 3,
                        )
                        actual_fires = []
                        fire = first
                        for _ in expected_fires:
                            actual_fires.append(fire)
                            fire = trigger.get_next_fire_time(
                                fire,
                                fire + timedelta(seconds=1),
                            )
                        self.assertEqual(actual_fires, expected_fires)
                        if bounded:
                            self.assertIsNone(fire)

    def test_ambiguous_start_keeps_first_occurrence_for_every_frequency(
        self,
    ) -> None:
        """A UTC start at the first DST fold stays eligible after storage."""
        for cron in (
            "30 1 * * *",
            "30 1 * * 0",
            "30 1 1 * *",
            "30 1 1 11 *",
        ):
            with self.subTest(cron=cron):
                self.storage.upserted.clear()
                self.scheduler.notified.clear()
                response = self.client.post(
                    "/schedule/",
                    json={
                        **_request(cron, "America/New_York").model_dump(
                            mode="json",
                        ),
                        "started_at": "2026-11-01T05:30:00Z",
                    },
                )
                self.assertEqual(response.status_code, 201, response.text)
                restored = ScheduleRecord.model_validate_json(
                    self.storage.upserted[0].model_dump_json(),
                )
                trigger = SchedulerManager.validate_schedule(restored)
                first = trigger.get_next_fire_time(
                    None,
                    datetime.fromisoformat("2026-10-30T00:00:00Z"),
                )
                self.assertIsNotNone(first)
                self.assertEqual(
                    (first.isoformat(), first.fold),
                    ("2026-11-01T01:30:00-04:00", 0),
                )

    def test_omitted_and_null_start_use_creation_time(self) -> None:
        """Both compatible request forms save an aware creation instant."""
        cases: list[dict[str, None]] = [{}, {"started_at": None}]
        for extra in cases:
            with self.subTest(extra=extra):
                self.storage.upserted.clear()
                self.scheduler.notified.clear()
                payload = {
                    **_request("0 * * * *").model_dump(
                        mode="json",
                        exclude={"started_at"},
                    ),
                    **extra,
                }
                before = datetime.now().astimezone()
                response = self.client.post("/schedule/", json=payload)
                after = datetime.now().astimezone()
                self.assertEqual(response.status_code, 201, response.text)
                record = self.storage.upserted[0]
                self.assertEqual(
                    response.json(),
                    {"schedule_id": record.id},
                )
                self.assertEqual(self.scheduler.notified, [record.id])
                self.assertIsNotNone(record.data.started_at.utcoffset())
                self.assertLessEqual(before, record.data.started_at)
                self.assertLessEqual(record.data.started_at, after)

    def test_invalid_start_time_has_no_write_side_effects(self) -> None:
        """Malformed/naive starts and invalid windows fail before mutation."""
        cases = (
            ("not-a-date", None, "datetime_from_date_parsing"),
            ("2026-11-01T20:26:00", None, "timezone_aware"),
            ("2026-02-30T20:26:00Z", None, "datetime_from_date_parsing"),
            ("2026-11-01T25:26:00Z", None, "datetime_from_date_parsing"),
            (
                "2026-11-01T20:26:00+08:00",
                "2026-11-01T12:26:00Z",
                None,
            ),
            (
                "2026-11-01T20:26:00+08:00",
                "2026-11-01T12:25:59Z",
                None,
            ),
        )
        for start, end, error_type in cases:
            with self.subTest(start=start, end=end):
                response = self.client.post(
                    "/schedule/",
                    json={
                        **_request("26 20 * * *").model_dump(mode="json"),
                        "started_at": start,
                        "ended_at": end,
                    },
                )
                self.assertEqual(response.status_code, 422, response.text)
                detail = response.json()["detail"]
                if error_type:
                    # Parser descriptions differ by the invalid component.
                    self.assertEqual(
                        [
                            (item["type"], item["loc"], item["input"])
                            for item in detail
                        ],
                        [(error_type, ["body", "started_at"], start)],
                    )
                else:
                    self.assertEqual(
                        response.json(),
                        {"detail": "ended_at must be later than started_at"},
                    )
                self.assertEqual(
                    (self.storage.upserted, self.scheduler.notified),
                    ([], []),
                )


class ScheduleWeekdayTest(TestCase):
    """Standard cron weekdays are translated for APScheduler."""

    def test_standard_weekdays(self) -> None:
        """Numeric weekdays count from Sunday; names pass through."""
        record = _record()
        actual = {}
        for expression in ("1", "0", "7", "1-5", "0-7/3", "mon-fri"):
            record.data.cron_expression = f"0 9 * * {expression}"
            trigger = SchedulerManager.validate_schedule(record)
            actual[expression] = str(trigger.fields[4])
        self.assertDictEqual(
            actual,
            {
                "1": "mon",
                "0": "sun",
                "7": "sun",
                "1-5": "mon,tue,wed,thu,fri",
                "0-7/3": "sun,wed,sat",
                "mon-fri": "mon-fri",
            },
        )

        record.data.cron_expression = "0 9 * * 8"
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
