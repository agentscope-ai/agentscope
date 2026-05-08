# -*- coding: utf-8 -*-
""""""
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import BaseModel

from .....message import ToolResultState, TextBlock
from .....permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .....tool import ToolBase, ToolChunk


class _ScheduleListParams(BaseModel):
    """The params for the schedule list tool."""

    pass


class ScheduleList(ToolBase):
    """The schedule list tool."""

    name: str = "ScheduleList"

    description: str = """List all scheduled jobs.
"""
    input_schema: dict = _ScheduleListParams.model_json_schema()

    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, scheduler: AsyncIOScheduler) -> None:
        """Initialize the schedule list.

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

    async def __call__(self) -> ToolChunk:
        """List all scheduled jobs."""
        schedules = self._scheduler.get_jobs()

        if not schedules:
            return ToolChunk(
                content=[
                    TextBlock(text="No scheduled jobs found."),
                ],
                state=ToolResultState.SUCCESS,
            )

        # Format schedule information
        schedule_info = []
        for job in schedules:
            info = f"ID: {job.id}\n"
            info += f"Name: {job.name}\n"
            info += f"Next run time: {job.next_run_time}\n"
            info += f"Trigger: {job.trigger}\n"
            schedule_info.append(info)

        result_text = f"Found {len(schedules)} scheduled job(s):\n\n"
        result_text += "\n---\n".join(schedule_info)

        return ToolChunk(
            content=[
                TextBlock(text=result_text),
            ],
            state=ToolResultState.SUCCESS,
        )
