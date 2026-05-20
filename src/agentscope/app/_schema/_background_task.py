# -*- coding: utf-8 -*-
"""Request / response schemas for the background-task router."""
from pydantic import BaseModel, Field


class BackgroundTaskInfo(BaseModel):
    """Summary of a single running background task."""

    task_id: str = Field(description="Unique task identifier.")
    session_id: str = Field(description="Session that owns this task.")
    agent_id: str = Field(description="Agent that triggered this task.")


class ListBackgroundTasksResponse(BaseModel):
    """Response body for listing background tasks of a session."""

    tasks: list[BackgroundTaskInfo] = Field(
        description="Running background tasks for the requested session.",
    )
    total: int = Field(description="Total number of running tasks.")
