# -*- coding: utf-8 -*-
"""The lifespan of the agent service."""
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from ._manager import BackgroundTaskManager, SchedulerManager, SessionManager

if TYPE_CHECKING:
    from fastapi import FastAPI
else:
    FastAPI = Any


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and shutdown of all application-wide resources.

    On startup:
    - Opens the storage connection pool.
    - Starts the session manager, background task manager, and scheduler.
    - Restores persisted schedules from storage into the in-memory scheduler.

    On shutdown:
    - Cancels in-flight sessions and background tasks.
    - Shuts down the scheduler (waits for running jobs to finish).
    """
    async with app.state.storage:
        session_manager = SessionManager()
        app.state.session_manager = session_manager
        app.state.background_task_manager = BackgroundTaskManager()

        scheduler = SchedulerManager(
            storage=app.state.storage,
            session_manager=session_manager,
            background_task_manager=app.state.background_task_manager,
            workspace_manager=app.state.workspace_manager,
        )
        app.state.scheduler_manager = scheduler
        await scheduler.start()

        # Restore persisted schedules so they survive server restarts
        all_schedules = await app.state.storage.list_all_schedules()
        if all_schedules:
            await scheduler.restore(all_schedules)

        yield

        app.state.session_manager.cancel()
        app.state.background_task_manager.cancel()
        await scheduler.shutdown()
