# -*- coding: utf-8 -*-
"""Background-task router — list and cancel per-session background tasks."""
from fastapi import APIRouter, Depends, HTTPException, status

from .._deps import get_background_task_manager, get_current_user_id
from .._manager import BackgroundTaskManager
from .._schema import (
    BackgroundTaskInfo,
    ListBackgroundTasksResponse,
)

background_task_router = APIRouter(
    prefix="/background-tasks",
    tags=["background-tasks"],
    responses={404: {"description": "Not found"}},
)


@background_task_router.get(
    "/{session_id}",
    response_model=ListBackgroundTasksResponse,
    summary="List running background tasks for a session",
)
async def list_background_tasks(
    session_id: str,
    _: str = Depends(get_current_user_id),
    bg_manager: BackgroundTaskManager = Depends(get_background_task_manager),
) -> ListBackgroundTasksResponse:
    """List all currently running background tasks for *session_id*.

    Args:
        session_id (`str`):
            The session to query.
        _ (`str`):
            The authenticated user ID (unused, required for auth check).
        bg_manager (`BackgroundTaskManager`):
            The application-wide background task manager.

    Returns:
        `ListBackgroundTasksResponse`:
            Tasks belonging to *session_id* and their total count.
    """
    tasks = [
        BackgroundTaskInfo(
            task_id=task.id,
            session_id=task.session_id,
            agent_id=task.agent_id,
        )
        for task in bg_manager.tasks.values()
        if task.session_id == session_id
    ]
    return ListBackgroundTasksResponse(tasks=tasks, total=len(tasks))


@background_task_router.delete(
    "/{session_id}/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a running background task",
)
async def cancel_background_task(
    session_id: str,
    task_id: str,
    _: str = Depends(get_current_user_id),
    bg_manager: BackgroundTaskManager = Depends(get_background_task_manager),
) -> None:
    """Cancel a running background task by *task_id*.

    The underlying asyncio task is cancelled immediately.  The
    ``on_complete`` callback will **not** be invoked, so no result
    notification is injected into the agent context.

    Args:
        session_id (`str`):
            The owning session (used to scope the lookup).
        task_id (`str`):
            The task to cancel.
        _ (`str`):
            The authenticated user ID (unused, required for auth check).
        bg_manager (`BackgroundTaskManager`):
            The application-wide background task manager.

    Raises:
        `HTTPException`:
            404 if *task_id* is not found or does not belong to
            *session_id*.
    """
    task = bg_manager.tasks.get(task_id)
    if task is None or task.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Background task '{task_id}' not found for session "
            f"'{session_id}'.",
        )

    bg_manager.tasks.pop(task_id, None)
    task.asyncio_task.cancel()
