# -*- coding: utf-8 -*-
""""""
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import BaseModel, Field

from .....message import ToolResultState, TextBlock
from .....permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .....tool import ToolBase, ToolChunk


class _ScheduleStopParams(BaseModel):
    """The params for the schedule stop tool."""

    schedule_id: str = Field(
        description="The schedule ID to stop.",
    )


class ScheduleStop(ToolBase):
    """The schedule stop tool."""

    name: str = "ScheduleStop"

    description: str = """Stop a running schedule by its ID.
"""
    input_schema: dict = _ScheduleStopParams.model_json_schema()

    is_concurrency_safe: bool = True
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, scheduler: AsyncIOScheduler) -> None:
        """Initialize the schedule stop.

        Args:
            scheduler (`AsyncIOScheduler`):
                The scheduler instance
        """
        self._scheduler = scheduler

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permission for the tool usage."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is always allowed to be called.",
        )

    async def __call__(self, schedule_id: str) -> ToolChunk:
        """Stop the schedule by its ID."""
        schedules = self._scheduler.get_jobs()

        schedule = None
        for job in schedules:
            if job.id == schedule_id:
                schedule = job
                break

        if schedule is None:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"ScheduleNotFoundError: Schedule with id {schedule_id} not found.",
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        # Remove the job from scheduler
        self._scheduler.remove_job(schedule_id)

        return ToolChunk(
            content=[
                TextBlock(
                    text=f"Schedule {schedule_id} has been stopped successfully.",
                ),
            ],
            state=ToolResultState.SUCCESS,
        )
