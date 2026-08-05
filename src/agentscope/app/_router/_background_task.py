# -*- coding: utf-8 -*-
"""Background-task router — read-only endpoints for task observability."""
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..deps import get_background_task_manager, get_current_user_id
from .._manager import BackgroundTaskManager
from ._schema import (
    BackgroundTaskInfo,
    ListBackgroundTasksResponse,
    to_info,
)

background_task_router = APIRouter(
    prefix="/background-tasks",
    tags=["background-tasks"],
    responses={404: {"description": "Not found"}},
)


@background_task_router.get(
    "",
    response_model=ListBackgroundTasksResponse,
    summary="List background tasks for the current user",
)
async def list_background_tasks(
    session_id: str
    | None = Query(
        default=None,
        description="Optional session filter.",
    ),
    user_id: str = Depends(get_current_user_id),
    bg_manager: BackgroundTaskManager = Depends(
        get_background_task_manager,
    ),
) -> ListBackgroundTasksResponse:
    """List all background tasks (running and recently-completed) visible
    to the authenticated user.

    Args:
        session_id (`str | None`, optional):
            If provided, only return tasks belonging to this session.
        user_id (`str`):
            The authenticated user id (from ``X-User-ID`` header).
        bg_manager (`BackgroundTaskManager`):
            The application-wide background task manager.

    Returns:
        `ListBackgroundTasksResponse`:
            Tasks and their total count.
    """
    tasks = bg_manager.list_tasks(
        user_id=user_id,
        session_id=session_id,
    )
    infos = [to_info(t) for t in tasks]
    return ListBackgroundTasksResponse(tasks=infos, total=len(infos))


@background_task_router.get(
    "/{task_id}",
    response_model=BackgroundTaskInfo,
    summary="Get a single background task by id",
)
async def get_background_task(
    task_id: str,
    user_id: str = Depends(get_current_user_id),
    bg_manager: BackgroundTaskManager = Depends(
        get_background_task_manager,
    ),
) -> BackgroundTaskInfo:
    """Retrieve details of a single background task.

    Returns 404 when the task does not exist or belongs to a different
    user (no information leakage about task existence).

    Args:
        task_id (`str`):
            The unique task identifier.
        user_id (`str`):
            The authenticated user id (from ``X-User-ID`` header).
        bg_manager (`BackgroundTaskManager`):
            The application-wide background task manager.

    Returns:
        `BackgroundTaskInfo`:
            The task details.

    Raises:
        `HTTPException`:
            404 if the task is not found or not owned by the user.
    """
    task = bg_manager.get_task(task_id, user_id=user_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Background task '{task_id}' not found.",
        )
    return to_info(task)
