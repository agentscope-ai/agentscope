# -*- coding: utf-8 -*-
""""""
from typing import Any, Callable, Coroutine

import shortuuid
from anthropic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic import Field

from ...permission import PermissionContext, PermissionDecision, \
    PermissionBehavior
from ...tool import ToolBase, ToolChunk


class _ScheduleViewParams(BaseModel):
    """The params for the schedule view tool."""
    schedule_id: str = Field(
        description="The schedule ID."
    )


class _ScheduleView(ToolBase):
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

    def __init__(self, schedules: list) -> None:
        """Initialize the schedule view.

        Args:
            schedules (list):
                Reference to the schedules.

        """
        self.schedules = schedules

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

    async def __call__(self, task_id: str) -> ToolChunk:


class SchedulerManager:
    """The cron scheduler manager, responsible for managing cron scheduler
    lifecycle within the agent service.
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.start()

    async def shutdown(self) -> None:
        self._scheduler.shutdown()

    def add_cron_task(
        self,
        coro_func: Callable[[], Coroutine],
        cron_expr: str,  # "0 2 * * *" 标准5段cron
        name: str = "",
    ) -> str:
        job = self._scheduler.add_job(
            coro_func,
            trigger=CronTrigger.from_crontab(cron_expr),
            id=shortuuid.uuid(),
            name=name,
        )
        return job.id

    def remove_task(self, job_id: str) -> None:
        self._scheduler.remove_job(job_id)

    def list_tasks(self) -> list[dict]:
        return [
            {
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time,
            }
            for job in self._scheduler.get_jobs()
        ]
