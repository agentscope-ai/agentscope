# -*- coding: utf-8 -*-
"""Schedule delete tool – removes a job from the scheduler and storage."""
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
from ....message_bus import MessageBus
from ....storage._base import StorageBase


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
        scheduler: Any,
        storage: StorageBase,
        message_bus: MessageBus,
    ) -> None:
        """Initialize the schedule delete tool.

        Args:
            user_id (`str`):
                The authenticated user; used to scope the storage deletion.
            scheduler (`Any`):
                The ``AsyncIOScheduler`` instance whose job will be removed.
            storage (`StorageBase`):
                The storage backend used to delete the persisted record.
            message_bus (`MessageBus`):
                The message bus used to cancel in-flight chat runs for
                any execution session spawned by this schedule and to
                purge their per-session bus state.
        """
        self._user_id = user_id
        self._scheduler = scheduler
        self._storage = storage
        self._message_bus = message_bus

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
        """Permanently delete the scheduled task with the given ID.

        Delegates the storage + bus cascade to
        :meth:`SessionService.delete_schedule`, which cancels in-flight
        runs for any session this schedule spawned and purges their
        bus state before dropping the schedule record. The APScheduler
        job is removed separately because it lives in-process and the
        service layer is bus/storage-only.

        Args:
            schedule_id (`str`):
                The unique identifier of the schedule to delete.

        Returns:
            `ToolChunk`:
                A chunk describing the result of the delete operation.
        """

        # Remove from the in-memory scheduler (best-effort; may already be
        # absent if the job finished naturally or the server restarted)
        try:
            self._scheduler.remove_job(schedule_id)
        except JobLookupError:
            pass

        # Local import to avoid a circular dependency between
        # ``_manager`` and ``_service`` at module load.
        from ...._service import SessionService  # noqa: PLC0415

        session_service = SessionService(
            storage=self._storage,
            message_bus=self._message_bus,
        )
        deleted = await session_service.delete_schedule(
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
