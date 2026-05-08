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


class _ScheduleViewParams(BaseModel):
    """The params for the schedule view tool."""

    schedule_id: str = Field(
        description="The schedule ID.",
    )


class ScheduleView(ToolBase):
    """The schedule view tool."""

    name: str = "ScheduleView"

    description: str = """
"""
    input_schema: dict = _ScheduleViewParams.model_json_schema()

    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, scheduler: AsyncIOScheduler) -> None:
        """Initialize the schedule view.

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
        """View the schedule detail."""
        schedules = self._scheduler.get_jobs()

        schedule = None
        for schedule in schedules:
            if schedule.id == schedule_id:
                schedule = schedule
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

        return ToolChunk(
            content=[
                TextBlock(
                    text="",
                ),
            ],
            state=ToolResultState.SUCCESS,
        )
