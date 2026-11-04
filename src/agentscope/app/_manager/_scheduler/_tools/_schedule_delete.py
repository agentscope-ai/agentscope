# -*- coding: utf-8 -*-
"""Schedule delete tool – removes a job from the scheduler and storage."""
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel, Field

from .....message import ToolResultState, TextBlock
from .....permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .....tool import ToolBase, ToolChunk

if TYPE_CHECKING:
    from .._scheduler_manager import SchedulerManager


class _ScheduleDeleteParams(BaseModel):
    """The params for the schedule delete tool."""

    schedule_id: str = Field(
        description="The schedule ID to delete (permanently remove).",
    )


class ScheduleDelete(ToolBase):
    """The schedule delete tool.

    Permanently removes the given scheduled job from APScheduler,
    storage, and the message bus. Every execution session spawned by
    the schedule is cancelled (if running) and has its bus state
    purged. The job cannot be recovered after removal.
    """

    name: str = "ScheduleDelete"

    description: str = (
        "Permanently delete a scheduled task by its schedule ID. "
        "After this call the task will no longer be executed and its record "
        "will be deleted from storage."
    )
    input_schema: dict = _ScheduleDeleteParams.model_json_schema()

    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        user_id: str,
        scheduler_manager: "SchedulerManager",
    ) -> None:
        """Initialize the schedule delete tool.

        Args:
            user_id (`str`):
                The authenticated user; used to scope the storage deletion.
            scheduler_manager (`SchedulerManager`):
                Coordinates the timer and session cleanup.
        """
        self._user_id = user_id
        self._scheduler_manager = scheduler_manager

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

    async def __call__(
        self,
        schedule_id: str,
    ) -> ToolChunk:  # type: ignore[override]
        """Permanently delete a schedule owned by the authenticated user.

        :meth:`SchedulerManager.delete_schedule` checks ownership and
        stops the timer if this node owns it, then deletes the schedule
        and its execution sessions.

        Args:
            schedule_id (`str`):
                The unique identifier of the schedule to delete.

        Returns:
            `ToolChunk`:
                A success chunk on deletion, or an error chunk if the
                schedule was not found for this user.
        """

        deleted = await self._scheduler_manager.delete_schedule(
            self._user_id,
            schedule_id,
        )

        if not deleted:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"ScheduleNotFoundError: Schedule with id "
                            f"{schedule_id!r} not found in storage."
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        return ToolChunk(
            content=[
                TextBlock(
                    text=(
                        f"Schedule {schedule_id!r} has been permanently "
                        f"deleted."
                    ),
                ),
            ],
            state=ToolResultState.SUCCESS,
        )
