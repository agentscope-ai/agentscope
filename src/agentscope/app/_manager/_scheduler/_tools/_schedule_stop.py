# -*- coding: utf-8 -*-
"""Schedule stop tool – removes a job from the scheduler."""
from typing import Any

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
        description="The schedule ID to stop (remove).",
    )


class ScheduleStop(ToolBase):
    """The schedule stop tool.

    Removes the given scheduled job from the scheduler so that it will no
    longer be triggered. The job cannot be recovered after removal.
    """

    name: str = "ScheduleStop"

    description: str = (
        "Stop (permanently remove) a scheduled job by its schedule ID. "
        "After this call the job will no longer be executed."
    )
    input_schema: dict = _ScheduleStopParams.model_json_schema()

    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(self, scheduler: Any) -> None:
        """Initialize the schedule stop tool.

        Args:
            scheduler (`AsyncIOScheduler`):
                The scheduler instance whose jobs can be removed.
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
        """Stop (remove) the scheduled job with the given ID.

        Args:
            schedule_id (`str`):
                The unique identifier of the schedule to stop.

        Returns:
            `ToolChunk`:
                A chunk describing the result of the stop operation.
        """
        from apscheduler.jobstores.base import JobLookupError

        try:
            self._scheduler.remove_job(schedule_id)
        except JobLookupError:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"ScheduleNotFoundError: Schedule with id "
                            f"{schedule_id!r} not found."
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        return ToolChunk(
            content=[
                TextBlock(
                    text=(
                        f"Schedule {schedule_id!r} has been stopped "
                        f"and removed successfully."
                    ),
                ),
            ],
            state=ToolResultState.SUCCESS,
        )
