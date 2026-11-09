# -*- coding: utf-8 -*-
"""The schedule view tool."""
from typing import Any

from pydantic import BaseModel, Field

from .....message import ToolResultState, TextBlock
from .....permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .....tool import ToolBase, ToolChunk
from ....storage import StorageBase


class _ScheduleViewParams(BaseModel):
    """The params for the schedule view tool."""

    schedule_id: str = Field(
        description="The schedule ID.",
    )


class ScheduleView(ToolBase):
    """The schedule view tool.

    Fetches the persisted :class:`ScheduleRecord` from storage and enriches
    it with the ``next_run_time`` from the in-memory APScheduler job.
    """

    name: str = "ScheduleView"

    description: str = (
        "View the full details of a scheduled task by its schedule ID, "
        "including cron expression, timezone, stateful flag, permission "
        "mode, and the next scheduled run time."
    )
    input_schema: dict = _ScheduleViewParams.model_json_schema()

    is_concurrency_safe: bool = True
    is_read_only: bool = True
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
        """Initialize the schedule view tool.

        Args:
            user_id (`str`):
                The authenticated user; used to scope the storage lookup.
            scheduler (`Any`):
                The ``AsyncIOScheduler`` instance for
                reading ``next_run_time``.
            storage (`StorageBase`):
                The storage backend that holds the persisted schedule records.
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
        """View the full details of a scheduled task.

        Args:
            schedule_id (`str`):
                The unique identifier of the schedule to view.

        Returns:
            `ToolChunk`:
                A chunk containing the formatted schedule details, or an
                error description if the schedule is not found.
        """
        record = await self._storage.get_schedule(self._user_id, schedule_id)

        if record is None:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"ScheduleNotFoundError: Schedule with "
                            f"id {schedule_id!r} not found."
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        job = self._scheduler.get_job(schedule_id)
        next_run = (
            str(job.next_run_time)
            if job is not None
            else "not in scheduler (may be disabled)"
        )
        enabled_str = "enabled" if record.data.enabled else "disabled"

        text = (
            f"Schedule ID:     {record.id}\n"
            f"Name:            {record.data.name}\n"
            f"Description:     {record.data.description or '(none)'}\n"
            f"Status:          {enabled_str}\n"
            f"Cron:            {record.data.cron_expression}"
            f" (timezone: {record.data.timezone})\n"
            f"Next run:        {next_run}\n"
            f"Stateful:        {record.data.stateful}\n"
            f"Permission mode: {record.data.permission_mode.value}\n"
            f"Source:          {record.data.source.value}\n"
            f"Source session:  {record.data.source_session_id or '(none)'}\n"
            f"Agent ID:        {record.agent_id}\n"
            f"Created at:      {record.created_at}\n"
            f"Updated at:      {record.updated_at}\n"
        )

        return ToolChunk(
            content=[TextBlock(text=text)],
            state=ToolResultState.SUCCESS,
        )
