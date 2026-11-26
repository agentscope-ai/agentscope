# -*- coding: utf-8 -*-
"""Request / response schemas for the schedule router."""
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, Field

from ...storage import (
    ScheduleSource,
    ChatModelConfig,
)
from ....permission import PermissionMode
from ...storage._model._base import _RecordBase
from ...storage._model._permission import AccessSettings


class CreateScheduleRequest(AccessSettings):
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

    started_at: AwareDatetime | None = Field(
        default=None,
        description="Inclusive start time of the schedule. Omit or set to "
        "null to use the creation time. Must include a UTC offset, e.g. "
        "'2026-11-01T20:26:00+08:00'.",
    )

    ended_at: AwareDatetime | None = Field(
        default=None,
        description="Inclusive end time of the schedule. Omit or set to null "
        "for no end boundary. Must include a UTC offset, e.g. "
        "'2026-10-31T23:59:59.999+08:00'; a naive value is rejected rather "
        "than guessed, because the stored record would lose the zone.",
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
        default=PermissionMode.AUTO,
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
    read_only: bool | None = Field(
        default=None,
        description="Restrict execution to read-only operations.",
    )
    allow_workspace_edits: bool | None = Field(
        default=None,
        description="Grant edits within authorized working directories.",
    )


class ListedScheduleData(AccessSettings):
    """Public configuration used by the task list and its detail drawer."""

    name: str
    description: str
    enabled: bool
    timezone: str
    cron_expression: str
    started_at: datetime
    ended_at: datetime | None
    chat_model_config: ChatModelConfig
    stateful: bool
    source: ScheduleSource
    source_session_id: str


class ListedSchedule(_RecordBase):
    """Schedule metadata returned to the task list."""

    user_id: str
    agent_id: str
    data: ListedScheduleData


class ListSchedulesResponse(BaseModel):
    """Response body for listing schedules."""

    schedules: list[ListedSchedule] = Field(description="Schedule records.")
    total: int = Field(description="Total number of schedules.")


class ScheduleSessionSummary(BaseModel):
    """Link to an execution in the schedule history."""

    id: str
    created_at: datetime


class ScheduleSessionsResponse(BaseModel):
    """Response body for listing execution sessions of a schedule."""

    sessions: list[ScheduleSessionSummary] = Field(
        description="Sessions triggered by this schedule.",
    )
    total: int = Field(description="Total number of execution sessions.")
