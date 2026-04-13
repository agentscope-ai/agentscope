# -*- coding: utf-8 -*-
""""""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..dependencies import get_task_manager
from ..models import TaskStatusResponse
from ..manager._task_stream import TaskStreamStatus

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    task_manager=Depends(get_task_manager),
) -> TaskStatusResponse:
    """查看任务状态"""
    stream = task_manager.get_stream(task_id)
    if stream is None:
        raise HTTPException(
            status_code=404,
            detail=f"Task {task_id} not found",
        )
    return TaskStatusResponse(
        task_id=stream.task_id,
        status=stream.status,
        created_at=stream.created_at,
        completed_at=stream.completed_at,
        error=stream.error,
    )


@router.delete("/{task_id}", response_model=TaskStatusResponse)
async def cancel_task(
    task_id: str,
    task_manager=Depends(get_task_manager),
) -> TaskStatusResponse:
    """取消任务"""
    # TODO: add user_id ownership check once auth is implemented
    stream = task_manager.get_stream(task_id)
    if stream is None:
        raise HTTPException(
            status_code=404,
            detail=f"Task {task_id} not found",
        )
    await stream.cancel()
    # Also cancel the underlying asyncio task if it's a tool task
    bg_task = task_manager._tasks.get(task_id)
    if bg_task and bg_task._asyncio_task:
        bg_task._asyncio_task.cancel()
    return TaskStatusResponse(
        task_id=stream.task_id,
        status=stream.status,
        created_at=stream.created_at,
        completed_at=stream.completed_at,
        error=stream.error,
    )


@router.get("/{task_id}/stream")
async def stream_task_output(
    task_id: str,
    replay_history: bool = True,
    task_manager=Depends(get_task_manager),
) -> StreamingResponse:
    """
    订阅任务的输出流（SSE）。先回放历史buffer，再实时追尾。
    任务已结束则只回放历史然后关闭连接。
    cron任务和后台工具任务共用此接口。
    """
    stream = task_manager.get_stream(task_id)
    if stream is None:
        raise HTTPException(
            status_code=404,
            detail=f"Task {task_id} not found",
        )

    async def event_generator():
        async for chunk in stream.subscribe(replay_history=replay_history):
            yield f"data: {chunk}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
