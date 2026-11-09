# -*- coding: utf-8 -*-
"""Request / response schemas for the background-task router."""
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from ..._manager._background_task_manager import (
    BackgroundTaskRecord,
    TaskStatus,
)


class BackgroundTaskInfo(BaseModel):
    """Summary of a single background task for API responses."""

    task_id: str = Field(description="Unique task identifier.")
    session_id: str = Field(description="Session that owns this task.")
    agent_id: str = Field(description="Agent that triggered this task.")
    tool_name: str = Field(description="Name of the offloaded tool.")
    summary: str = Field(
        description="Short human-readable summary of the tool invocation. "
        "May include unredacted tool arguments stored in shared task records.",
    )
    status: TaskStatus = Field(description="Current lifecycle status.")
    started_at: datetime = Field(
        description="UTC timestamp when the task was registered.",
    )
    completed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the task reached a terminal "
        "state, or null while still running.",
    )
    error_summary: str | None = Field(
        default=None,
        description="Brief failure description (only for failed tasks).",
    )


class ListBackgroundTasksResponse(BaseModel):
    """Response body for listing background tasks."""

    tasks: list[BackgroundTaskInfo] = Field(
        description="Background tasks for the requested scope.",
    )
    total: int = Field(description="Total number of tasks returned.")


def to_info(task: BackgroundTaskRecord) -> BackgroundTaskInfo:
    """Convert shared task metadata to an API-facing schema.

    Args:
        task (`BackgroundTaskRecord`):
            The shared background task metadata.

    Returns:
        `BackgroundTaskInfo`:
            The serializable API response model.
    """
    return BackgroundTaskInfo(
        task_id=task.id,
        session_id=task.session_id,
        agent_id=task.agent_id,
        tool_name=task.tool_name,
        summary=task.summary,
        status=task.status,
        started_at=datetime.fromtimestamp(
            task.started_at,
            tz=timezone.utc,
        ),
        completed_at=(
            datetime.fromtimestamp(task.completed_at, tz=timezone.utc)
            if task.completed_at is not None
            else None
        ),
        error_summary=task.error_summary,
    )
