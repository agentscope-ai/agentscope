# -*- coding: utf-8 -*-
"""Request / response schemas for the schedule router."""
from pydantic import BaseModel, Field

from ...storage import (
    ScheduleRecord,
    SessionRecord,
    ChatModelConfig,
)
from ....permission import PermissionMode


class CreateScheduleRequest(BaseModel):
    """Request body for creating a new schedule."""

    name: str = Field(description="Display name of the schedule.")

    description: str = Field(default="", description="Optional description.")

    cron_expression: str = Field(
        description="Standard 5-field cron expression, e.g. '0 9 * * 1-5'.",
    )

    timezone: str = Field(
        default="UTC",
        description="IANA timezone name, e.g. 'America/New_York' or "
        "'Asia/Shanghai'.",
    )

    agent_id: str = Field(description="Agent to run when the schedule fires.")

    chat_model_config: ChatModelConfig = Field(
        description="Model configuration for the auto-created session.",
    )

    enabled: bool = Field(
        default=True,
        description="Whether the schedule is active immediately "
        "after creation.",
    )

    stateful: bool = Field(
        default=False,
        description="If True, consecutive executions share the same session "
        "context.",
    )

    permission_mode: PermissionMode = Field(
        default=PermissionMode.DONT_ASK,
        description="Permission level for the agent during "
        "scheduled execution.",
    )


class CreateScheduleResponse(BaseModel):
    """Response body after creating a schedule."""

    schedule_id: str = Field(
        description="Server-assigned schedule identifier.",
    )


class UpdateScheduleRequest(BaseModel):
    """Request body for partially updating a schedule.

    Omit any field to keep its current value.  Changing ``cron_expression``
    or ``timezone`` will reschedule the APScheduler job immediately.
    Changing ``enable`` to ``False`` removes the job from the scheduler
    without deleting the record; setting it back to ``True`` re-registers it.
    """

    name: str | None = Field(default=None, description="New display name.")

    description: str | None = Field(
        default=None,
        description="New description.",
    )

    cron_expression: str | None = Field(
        default=None,
        description="New cron expression. Reschedules the task immediately.",
    )

    timezone: str | None = Field(
        default=None,
        description="New IANA timezone name.",
    )

    enabled: bool | None = Field(
        default=None,
        description="Set to False to pause the schedule without deleting it.",
    )

    stateful: bool | None = Field(
        default=None,
        description="Change whether executions share session context.",
    )

    permission_mode: PermissionMode | None = Field(
        default=None,
        description="New permission mode.",
    )


class ListSchedulesResponse(BaseModel):
    """Response body for listing schedules."""

    schedules: list[ScheduleRecord] = Field(description="Schedule records.")
    total: int = Field(description="Total number of schedules.")


class ScheduleSessionsResponse(BaseModel):
    """Response body for listing execution sessions of a schedule."""

    sessions: list[SessionRecord] = Field(
        description="Sessions triggered by this schedule.",
    )
    total: int = Field(description="Total number of execution sessions.")
