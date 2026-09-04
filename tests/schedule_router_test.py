# -*- coding: utf-8 -*-
"""Tests for schedule validation and optional channel association."""
from datetime import datetime
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException, status

from agentscope.app._manager import SchedulerManager
from agentscope.app._manager._scheduler._tools._schedule_create import (
    ScheduleCreate,
)
from agentscope.app._router._channel import _to_response
from agentscope.app._router._schedule import create_schedule, update_schedule
from agentscope.app._router._schema._schedule import (
    CreateScheduleRequest,
    UpdateScheduleRequest,
)
from agentscope.app.storage import (
    ChatModelConfig,
    ChannelBinding,
    ChannelRecord,
    RoutingConfig,
    ScheduleData,
    ScheduleRecord,
    SessionSettings,
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


def _chat_model_config() -> ChatModelConfig:
    """Return the minimal model configuration used by schedule tests."""
    return ChatModelConfig(
        type="test_credential",
        credential_id="credential-1",
        model="test-model",
        parameters={},
    )


def _schedule_record(
    *,
    channel_id: str | None = "channel-1",
) -> ScheduleRecord:
    """Return a persisted schedule owned by ``owner``."""
    return ScheduleRecord(
        id="schedule-1",
        user_id="owner",
        agent_id="agent-1",
        data=ScheduleData(
            name="daily summary",
            description="existing description",
            cron_expression="0 9 * * *",
            started_at=datetime(2025, 1, 1),
            chat_model_config=_chat_model_config(),
            channel_id=channel_id,
        ),
    )


class ScheduleRouterChannelTest(IsolatedAsyncioTestCase):
    """Channel association is persisted and ownership-checked."""

    def setUp(self) -> None:
        """Build lightweight async dependencies for direct router calls."""
        self.storage = AsyncMock()
        self.access = AsyncMock()
        self.scheduler = AsyncMock()
        self.scheduler.validate_schedule = MagicMock()
        self.registry = MagicMock()
        self.registry.get.return_value = SimpleNamespace(
            supports_scheduled_tools=True,
        )

    def test_legacy_schedule_data_defaults_channel_to_none(self) -> None:
        """Stored schedules written before the field was added still load."""
        data = ScheduleData(
            name="legacy",
            cron_expression="0 9 * * *",
            chat_model_config=_chat_model_config(),
        )

        self.assertIsNone(data.channel_id)

    async def test_create_persists_owned_channel(self) -> None:
        """Creating a schedule accepts a channel owned by the caller."""
        self.storage.get_channel.return_value = SimpleNamespace(
            user_id="owner",
            channel_type="feishu",
        )
        body = CreateScheduleRequest(
            name="daily summary",
            cron_expression="0 9 * * *",
            agent_id="agent-1",
            chat_model_config=_chat_model_config(),
            channel_id="channel-1",
        )

        await create_schedule(
            body,
            user_id="owner",
            storage=self.storage,
            registry=self.registry,
            access=self.access,
            scheduler=self.scheduler,
        )

        self.storage.get_channel.assert_awaited_once_with("channel-1")
        record = self.storage.upsert_schedule.await_args.args[1]
        self.assertEqual(record.data.channel_id, "channel-1")
        self.scheduler.validate_schedule.assert_called_once_with(record)
        self.scheduler.notify_changed.assert_awaited_once_with(record.id)

    async def test_create_without_channel_skips_channel_storage(self) -> None:
        """The optional field preserves storage-backend compatibility."""
        body = CreateScheduleRequest(
            name="daily summary",
            cron_expression="0 9 * * *",
            agent_id="agent-1",
            chat_model_config=_chat_model_config(),
        )

        await create_schedule(
            body,
            user_id="owner",
            storage=self.storage,
            registry=self.registry,
            access=self.access,
            scheduler=self.scheduler,
        )

        self.storage.get_channel.assert_not_awaited()
        record = self.storage.upsert_schedule.await_args.args[1]
        self.assertIsNone(record.data.channel_id)
        self.scheduler.validate_schedule.assert_called_once_with(record)
        self.scheduler.notify_changed.assert_awaited_once_with(record.id)

    async def test_create_rejects_missing_channel(self) -> None:
        """A nonexistent channel cannot be attached to a new schedule."""
        self.storage.get_channel.return_value = None
        body = CreateScheduleRequest(
            name="daily summary",
            cron_expression="0 9 * * *",
            agent_id="agent-1",
            chat_model_config=_chat_model_config(),
            channel_id="missing",
        )

        with self.assertRaises(HTTPException) as ctx:
            await create_schedule(
                body,
                user_id="owner",
                storage=self.storage,
                registry=self.registry,
                access=self.access,
                scheduler=self.scheduler,
            )

        self.assertEqual(ctx.exception.status_code, status.HTTP_404_NOT_FOUND)
        self.storage.upsert_schedule.assert_not_awaited()
        self.scheduler.notify_changed.assert_not_awaited()

    async def test_create_rejects_channel_owned_by_another_user(self) -> None:
        """A caller cannot attach another user's channel."""
        self.storage.get_channel.return_value = SimpleNamespace(
            user_id="another-user",
            channel_type="feishu",
        )
        body = CreateScheduleRequest(
            name="daily summary",
            cron_expression="0 9 * * *",
            agent_id="agent-1",
            chat_model_config=_chat_model_config(),
            channel_id="channel-1",
        )

        with self.assertRaises(HTTPException) as ctx:
            await create_schedule(
                body,
                user_id="owner",
                storage=self.storage,
                registry=self.registry,
                access=self.access,
                scheduler=self.scheduler,
            )

        self.assertEqual(ctx.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.storage.upsert_schedule.assert_not_awaited()

    async def test_create_rejects_channel_without_scheduled_tools(
        self,
    ) -> None:
        """A registered adapter must explicitly opt in to scheduled tools."""
        self.storage.get_channel.return_value = SimpleNamespace(
            user_id="owner",
            channel_type="discord",
        )
        self.registry.get.return_value = SimpleNamespace(
            supports_scheduled_tools=False,
        )
        body = CreateScheduleRequest(
            name="daily summary",
            cron_expression="0 9 * * *",
            agent_id="agent-1",
            chat_model_config=_chat_model_config(),
            channel_id="channel-1",
        )

        with self.assertRaises(HTTPException) as ctx:
            await create_schedule(
                body,
                user_id="owner",
                storage=self.storage,
                registry=self.registry,
                access=self.access,
                scheduler=self.scheduler,
            )

        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.storage.upsert_schedule.assert_not_awaited()

    async def test_create_rejects_unregistered_channel_type(self) -> None:
        """A stale channel type cannot be attached through the API."""
        self.storage.get_channel.return_value = SimpleNamespace(
            user_id="owner",
            channel_type="removed-adapter",
        )
        self.registry.get.return_value = None
        body = CreateScheduleRequest(
            name="daily summary",
            cron_expression="0 9 * * *",
            agent_id="agent-1",
            chat_model_config=_chat_model_config(),
            channel_id="channel-1",
        )

        with self.assertRaises(HTTPException) as ctx:
            await create_schedule(
                body,
                user_id="owner",
                storage=self.storage,
                registry=self.registry,
                access=self.access,
                scheduler=self.scheduler,
            )

        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.storage.upsert_schedule.assert_not_awaited()

    async def test_update_omitted_channel_keeps_existing_value(self) -> None:
        """An omitted channel field leaves the association unchanged."""
        self.storage.get_schedule.return_value = _schedule_record()

        updated = await update_schedule(
            "schedule-1",
            UpdateScheduleRequest(description="new description"),
            user_id="owner",
            storage=self.storage,
            registry=self.registry,
            scheduler=self.scheduler,
        )

        self.assertEqual(updated.data.channel_id, "channel-1")
        self.assertEqual(updated.data.description, "new description")
        self.storage.get_channel.assert_not_awaited()

    async def test_update_explicit_null_clears_only_channel(self) -> None:
        """Explicit null clears channel; other null fields stay ignored."""
        self.storage.get_schedule.return_value = _schedule_record()

        updated = await update_schedule(
            "schedule-1",
            UpdateScheduleRequest(channel_id=None, description=None),
            user_id="owner",
            storage=self.storage,
            registry=self.registry,
            scheduler=self.scheduler,
        )

        self.assertIsNone(updated.data.channel_id)
        self.assertEqual(updated.data.description, "existing description")
        self.storage.get_channel.assert_not_awaited()

    async def test_update_validates_replacement_channel(self) -> None:
        """Replacing a channel checks ownership before persisting."""
        self.storage.get_schedule.return_value = _schedule_record()
        self.storage.get_channel.return_value = SimpleNamespace(
            user_id="another-user",
            channel_type="feishu",
        )

        with self.assertRaises(HTTPException) as ctx:
            await update_schedule(
                "schedule-1",
                UpdateScheduleRequest(channel_id="channel-2"),
                user_id="owner",
                storage=self.storage,
                registry=self.registry,
                scheduler=self.scheduler,
            )

        self.assertEqual(ctx.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.storage.upsert_schedule.assert_not_awaited()
        self.scheduler.notify_changed.assert_not_awaited()

    async def test_update_persists_owned_replacement_channel(self) -> None:
        """An owned replacement channel is stored and notifies the scheduler
        owner."""
        self.storage.get_schedule.return_value = _schedule_record(
            channel_id=None,
        )
        self.storage.get_channel.return_value = SimpleNamespace(
            user_id="owner",
            channel_type="feishu",
        )

        updated = await update_schedule(
            "schedule-1",
            UpdateScheduleRequest(channel_id="channel-2"),
            user_id="owner",
            storage=self.storage,
            registry=self.registry,
            scheduler=self.scheduler,
        )

        self.assertEqual(updated.data.channel_id, "channel-2")
        self.storage.get_channel.assert_awaited_once_with("channel-2")
        self.storage.upsert_schedule.assert_awaited_once()
        self.scheduler.validate_schedule.assert_called_once_with(updated)
        self.scheduler.notify_changed.assert_awaited_once_with("schedule-1")

    async def test_update_rejects_channel_without_scheduled_tools(
        self,
    ) -> None:
        """An unsupported replacement cannot alter or notify a task."""
        self.storage.get_schedule.return_value = _schedule_record()
        self.storage.get_channel.return_value = SimpleNamespace(
            user_id="owner",
            channel_type="discord",
        )
        self.registry.get.return_value = SimpleNamespace(
            supports_scheduled_tools=False,
        )

        with self.assertRaises(HTTPException) as ctx:
            await update_schedule(
                "schedule-1",
                UpdateScheduleRequest(channel_id="channel-2"),
                user_id="owner",
                storage=self.storage,
                registry=self.registry,
                scheduler=self.scheduler,
            )

        self.assertEqual(
            ctx.exception.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.storage.upsert_schedule.assert_not_awaited()
        self.scheduler.notify_changed.assert_not_awaited()


class ChannelResponseCapabilityTest(TestCase):
    """Channel list responses expose scheduled-tool support to the UI."""

    @staticmethod
    def _record(channel_type: str) -> ChannelRecord:
        """Build a minimal persisted channel record."""
        return ChannelRecord(
            id=f"{channel_type}-channel",
            channel_type=channel_type,
            name=channel_type,
            user_id="owner",
            enabled=False,
            credentials={},
            platform_config={},
            routing=RoutingConfig(
                bindings=[ChannelBinding(agent_id="agent-1")],
            ),
            session=SessionSettings(chat_model_config={}),
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        )

    def test_response_derives_capability_from_registered_adapter(self) -> None:
        """Supported disabled channels remain selectable; others do not."""
        registry = MagicMock()
        registry.extract_platform_bot_id.return_value = "bot-id"

        registry.get.return_value = SimpleNamespace(
            supports_scheduled_tools=True,
        )
        feishu = _to_response(self._record("feishu"), registry)

        registry.get.return_value = SimpleNamespace(
            supports_scheduled_tools=False,
        )
        discord = _to_response(self._record("discord"), registry)

        self.assertTrue(feishu.supports_scheduled_tools)
        self.assertFalse(feishu.enabled)
        self.assertFalse(discord.supports_scheduled_tools)
