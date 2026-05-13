# -*- coding: utf-8 -*-
"""Request / response schemas for the schedule router."""
from pydantic import BaseModel, Field

from ..storage._model._schedule import ScheduleRecord
from ..storage._model._session import ChatModelConfig
from ...permission._types import PermissionMode


class CreateScheduleRequest(BaseModel):
    """Request body for creating a new schedule."""

    name: str = Field(description="Display name of the schedule.")
    description: str = Field(default="", description="Optional description.")
    cron_expression: str = Field(
        description="Standard 5-field cron expression, e.g. '0 9 * * 1-5'.",
    )
    agent_id: str = Field(description="Agent to run when the schedule fires.")
    workspace_id: str = Field(
        description="Workspace used when creating the triggered session.",
    )
    chat_model_config: ChatModelConfig = Field(
        description="Model configuration for the auto-created session.",
    )
    input: dict | None = Field(
        default=None,
        description="Serialised Msg sent to the agent on each trigger.",
    )
    permission_mode: PermissionMode = Field(
        default=PermissionMode.DONT_ASK,
        description="Permission level for the agent during scheduled execution.",
    )


class CreateScheduleResponse(BaseModel):
    """Response body after creating a schedule."""

    schedule_id: str = Field(
        description="Server-assigned schedule identifier.",
    )


class UpdateScheduleRequest(BaseModel):
    """Request body for partially updating a schedule.

    Omit any field to keep its current value.
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
    input: dict | None = Field(
        default=None,
        description="New trigger input. Pass an empty dict to clear.",
    )


class ScheduleListResponse(BaseModel):
    """Response body for listing schedules."""

    schedules: list[ScheduleRecord] = Field(description="Schedule records.")
    total: int = Field(description="Total number of schedules.")
