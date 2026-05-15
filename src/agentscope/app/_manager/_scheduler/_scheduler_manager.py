# -*- coding: utf-8 -*-
"""The cron scheduler manager class."""
from typing import Callable, Coroutine

import shortuuid
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ....tool import ToolBase
from ._tools import ScheduleView, ScheduleStop, ScheduleList


class SchedulerManager:
    """The cron scheduler manager, responsible for managing cron scheduler
    lifecycle within the agent service.
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    async def start(self) -> None:
        self._scheduler.start()

    async def shutdown(self) -> None:
        self._scheduler.shutdown()

    async def add_schedule(
        self,
        coro_func: Callable[[], Coroutine],
        cron_expr: str,
        name: str = "",
        job_id: str | None = None,
    ) -> str:
        job = self._scheduler.add_job(
            coro_func,
            trigger=CronTrigger.from_crontab(cron_expr),
            id=job_id or shortuuid.uuid(),
            name=name,
        )
        return job.id

    async def restore(
        self,
        tasks: list,
        task_factory: Callable,
    ) -> None:
        """Re-register persisted schedules on startup.

        Args:
            tasks: List of ScheduleRecord loaded from storage.
            task_factory: Callable that takes a ScheduleRecord and returns
                a zero-argument coroutine function to pass to APScheduler.
        """
        for task in tasks:
            coro_func = task_factory(task)
            await self.add_schedule(
                coro_func,
                task.data.cron_expression,
                name=task.data.name,
                job_id=task.id,
            )

    async def remove_task(self, job_id: str) -> None:
        self._scheduler.remove_job(job_id)

    async def list_tasks(self) -> list[dict]:
        return [
            {
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time,
            }
            for job in self._scheduler.get_jobs()
        ]

    async def list_tools(self) -> list[ToolBase]:
        """List the agent tools provided by the scheduler manager."""
        return [
            ScheduleView(self._scheduler),
            ScheduleStop(self._scheduler),
            ScheduleList(self._scheduler),
        ]
