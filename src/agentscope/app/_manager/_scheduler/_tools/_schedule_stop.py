# -*- coding: utf-8 -*-
"""Schedule stop tool – removes a job from the scheduler and storage."""
from typing import Any

from pydantic import BaseModel, Field
from apscheduler.jobstores.base import JobLookupError

from .....message import ToolResultState, TextBlock
from .....permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .....tool import ToolBase, ToolChunk
from ....storage._base import StorageBase


class _ScheduleStopParams(BaseModel):
    """The params for the schedule stop tool."""

    schedule_id: str = Field(
        description="The schedule ID to stop (remove).",
    )


class ScheduleStop(ToolBase):
    """The schedule stop tool.

    Permanently removes the given scheduled job from both the in-memory
    APScheduler and the persistent storage.  The job cannot be recovered
    after removal.
    """

    name: str = "ScheduleStop"

    description: str = (
        "Stop (permanently remove) a scheduled task by its schedule ID. "
        "After this call the task will no longer be executed and its record "
        "will be deleted from storage."
    )
    input_schema: dict = _ScheduleStopParams.model_json_schema()

    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        user_id: str,
        scheduler: Any,
        storage: StorageBase,
    ) -> None:
        """Initialize the schedule stop tool.

        Args:
            user_id (`str`):
                The authenticated user; used to scope the storage deletion.
            scheduler (`Any`):
                The ``AsyncIOScheduler`` instance whose job will be removed.
            storage (`StorageBase`):
                The storage backend used to delete the persisted record.
        """
        self._user_id = user_id
        self._scheduler = scheduler
        self._storage = storage

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
        """Stop (remove) the scheduled task with the given ID.

        Args:
            schedule_id (`str`):
                The unique identifier of the schedule to stop.

        Returns:
            `ToolChunk`:
                A chunk describing the result of the stop operation.
        """

        # Remove from the in-memory scheduler (best-effort; may already be
        # absent if the job finished naturally or the server restarted)
        try:
            self._scheduler.remove_job(schedule_id)
        except JobLookupError:
            pass

        # Delete from persistent storage
        deleted = await self._storage.delete_schedule(
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
                        f"Schedule {schedule_id!r} has been stopped "
                        f"and permanently removed."
                    ),
                ),
            ],
            state=ToolResultState.SUCCESS,
        )
