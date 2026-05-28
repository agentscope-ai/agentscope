# -*- coding: utf-8 -*-
"""The lifespan of the agent service."""
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
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
    - Enters the workspace manager (starts its TTL sweeper, if any).
    - Starts the session manager, background task manager, and scheduler.
    - Restores persisted schedules from storage into the in-memory scheduler.

    On shutdown:
    - Cancels in-flight sessions and background tasks.
    - Shuts down the scheduler (waits for running jobs to finish).
    - Exits the workspace manager (stops its sweeper and closes every
      cached workspace).
    """
    workspace_manager = app.state.workspace_manager
    workspace_ctx = (
        workspace_manager if workspace_manager is not None else nullcontext()
    )

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app.state.storage)
        await stack.enter_async_context(workspace_ctx)

        session_manager = SessionManager()
        app.state.session_manager = session_manager
        app.state.background_task_manager = BackgroundTaskManager()

        scheduler = SchedulerManager(
            storage=app.state.storage,
            session_manager=session_manager,
            background_task_manager=app.state.background_task_manager,
            workspace_manager=workspace_manager,
            extra_agent_middlewares=app.state.extra_agent_middlewares,
            extra_agent_tools=app.state.extra_agent_tools,
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
