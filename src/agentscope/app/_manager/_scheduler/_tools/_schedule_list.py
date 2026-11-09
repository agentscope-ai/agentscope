# -*- coding: utf-8 -*-
"""The tool to list the scheduled jobs in the cron scheduler manager."""
from typing import Any

from pydantic import BaseModel

from .....message import ToolResultState, TextBlock
from .....permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .....tool import ToolBase, ToolChunk
from ....storage import StorageBase


class _ScheduleListParams(BaseModel):
    """The params for the schedule list tool."""


class ScheduleList(ToolBase):
    """The schedule list tool.

    Lists all scheduled tasks owned by the current user.  Each entry is
    fetched from storage (rich :class:`ScheduleData`) and augmented with
    ``next_run_time`` from the in-memory APScheduler job when available.
    """

    name: str = "ScheduleList"

    description: str = (
        "List all scheduled tasks for the current user. "
        "Shows schedule ID, name, cron expression, timezone, next run time, "
        "enabled/disabled status, and whether the schedule is stateful."
    )
    input_schema: dict = _ScheduleListParams.model_json_schema()

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
        """Initialize the schedule list tool.

        Args:
            user_id (`str`):
                The authenticated user; used to scope the storage lookup.
            scheduler (`Any`):
                The ``AsyncIOScheduler`` instance for reading ``next_run_time``
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

    async def __call__(self) -> ToolChunk:  # type: ignore[override]
        """List all scheduled tasks for the current user.

        Returns:
            `ToolChunk`:
                A chunk containing a formatted list of all scheduled tasks,
                or a message indicating none exist.
        """
        records = await self._storage.list_schedules(self._user_id)

        if not records:
            return ToolChunk(
                content=[TextBlock(text="No scheduled tasks found.")],
                state=ToolResultState.SUCCESS,
            )

        # Build a map of schedule_id -> next_run_time from the live scheduler
        next_run_map: dict[str, str] = {
            job.id: str(job.next_run_time)
            for job in self._scheduler.get_jobs()
        }

        lines: list[str] = [f"Found {len(records)} scheduled task(s):\n"]
        for record in records:
            enabled_str = "enabled" if record.data.enabled else "disabled"
            next_run = next_run_map.get(record.id, "not in scheduler")
            lines.append(
                f"- [{enabled_str}] {record.data.name!r}  (ID: {record.id})\n"
                f"  Cron:      {record.data.cron_expression}"
                f" ({record.data.timezone})\n"
                f"  Next run:  {next_run}\n"
                f"  Stateful:  {record.data.stateful}"
                f"  |  Agent: {record.agent_id}\n"
                f"  Source:    {record.data.source.value}\n",
            )

        return ToolChunk(
            content=[TextBlock(text="\n".join(lines))],
            state=ToolResultState.SUCCESS,
        )
