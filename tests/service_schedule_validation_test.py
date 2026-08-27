# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for validating schedules before mutating persistent state."""
from datetime import datetime
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock

from fastapi import HTTPException

from agentscope.app._manager import SchedulerManager
from agentscope.app._manager._scheduler._tools import ScheduleCreate
from agentscope.app._router._schedule import create_schedule, update_schedule
from agentscope.app._router._schema import (
    CreateScheduleRequest,
    UpdateScheduleRequest,
)
from agentscope.app.storage import (
    ChatModelConfig,
    ScheduleData,
    ScheduleRecord,
)


def _chat_model_config() -> ChatModelConfig:
    return ChatModelConfig(
        type="dashscope_credential",
        credential_id="credential-id",
        model="model-name",
        parameters={},
    )


def _schedule_record(cron_expression: str = "0 0 * * *") -> ScheduleRecord:
    return ScheduleRecord(
        user_id="user-id",
        agent_id="agent-id",
        data=ScheduleData(
            name="schedule",
            cron_expression=cron_expression,
            timezone="UTC",
            started_at=datetime(2026, 1, 1),
            chat_model_config=_chat_model_config(),
        ),
    )


class TestSchedulerValidation(TestCase):
    """The manager validates cron configuration without adding a job."""

    def test_invalid_cron_does_not_add_scheduler_job(self) -> None:
        """Invalid cron input does not create an APScheduler job."""
        manager = SchedulerManager(Mock(), Mock(), Mock())

        with self.assertRaisesRegex(ValueError, "5-field cron expression"):
            manager.validate_schedule(_schedule_record("not a cron"))

        self.assertEqual(manager._scheduler.get_jobs(), [])


class TestScheduleMutationOrdering(IsolatedAsyncioTestCase):
    """Invalid schedules are rejected before persistence or job mutation."""

    async def test_create_rejects_invalid_schedule_before_persisting(
        self,
    ) -> None:
        """The create endpoint returns 422 before writing storage."""
        storage = AsyncMock()
        access = AsyncMock()
        scheduler = Mock()
        scheduler.validate_schedule.side_effect = ValueError("invalid cron")
        body = CreateScheduleRequest(
            name="invalid",
            cron_expression="not a cron",
            agent_id="agent-id",
            chat_model_config=_chat_model_config(),
        )

        with self.assertRaises(HTTPException) as context:
            await create_schedule(
                body,
                user_id="user-id",
                storage=storage,
                access=access,
                scheduler=scheduler,
            )

        self.assertEqual(context.exception.status_code, 422)
        storage.upsert_schedule.assert_not_awaited()

    async def test_update_rejects_invalid_schedule_before_mutating(
        self,
    ) -> None:
        """A failed update preserves storage and the current job."""
        storage = AsyncMock()
        storage.get_schedule.return_value = _schedule_record()
        scheduler = Mock()
        scheduler.validate_schedule.side_effect = ValueError("invalid cron")

        with self.assertRaises(HTTPException) as context:
            await update_schedule(
                "schedule-id",
                UpdateScheduleRequest(cron_expression="not a cron"),
                user_id="user-id",
                storage=storage,
                scheduler=scheduler,
            )

        self.assertEqual(context.exception.status_code, 422)
        storage.upsert_schedule.assert_not_awaited()
        scheduler.remove_schedule.assert_not_called()

    async def test_agent_tool_validates_before_persisting(self) -> None:
        """The agent tool validates before writing its schedule record."""
        storage = AsyncMock()
        scheduler = Mock()
        scheduler.validate_schedule.side_effect = ValueError("invalid cron")
        tool = ScheduleCreate(
            user_id="user-id",
            agent_id="agent-id",
            chat_model_config=_chat_model_config(),
            storage=storage,
            scheduler_manager=scheduler,
        )

        with self.assertRaisesRegex(ValueError, "invalid cron"):
            await tool(name="invalid", cron_expression="not a cron")

        storage.upsert_schedule.assert_not_awaited()
