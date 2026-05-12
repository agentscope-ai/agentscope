# -*- coding: utf-8 -*-
"""Schedule router — CRUD endpoints for scheduled agent tasks."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status

from .._deps import get_current_user_id, get_scheduler_manager, get_storage
from .._manager import SchedulerManager
from .._schema._schedule import (
    CreateScheduleRequest,
    CreateScheduleResponse,
    ScheduleListResponse,
    UpdateScheduleRequest,
)
from ..storage._base import StorageBase
from ..storage._model._schedule import ScheduleData, ScheduleRecord

schedule_router = APIRouter(
    prefix="/schedule",
    tags=["schedule"],
    responses={404: {"description": "Not found"}},
)


def _build_trigger(record: ScheduleRecord, storage: StorageBase) -> callable:
    async def trigger() -> None:
        # TODO: implement chat trigger logic
        # 1. upsert_session(record.user_id, record.data.agent_id,
        #                   record.data.workspace_id, SessionData(...))
        # 2. assemble Agent from storage
        # 3. run agent with record.data.input
        pass

    return trigger


@schedule_router.get(
    "/",
    response_model=ScheduleListResponse,
    summary="List all schedules",
)
async def list_schedules(
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> ScheduleListResponse:
    schedules = await storage.list_schedules(user_id)
    return ScheduleListResponse(schedules=schedules, total=len(schedules))


@schedule_router.post(
    "/",
    response_model=CreateScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new schedule",
)
async def create_schedule(
    body: CreateScheduleRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    scheduler: SchedulerManager = Depends(get_scheduler_manager),
) -> CreateScheduleResponse:
    agents = await storage.list_agent(user_id)
    if not any(a.data.id == body.agent_id for a in agents):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{body.agent_id}' not found.",
        )

    record = ScheduleRecord(
        user_id=user_id,
        data=ScheduleData(
            name=body.name,
            description=body.description,
            cron_expression=body.cron_expression,
            agent_id=body.agent_id,
            workspace_id=body.workspace_id,
            chat_model_config=body.chat_model_config,
            input=body.input,
        ),
    )
    await storage.create_schedule(user_id, record)
    await scheduler.add_schedule(
        _build_trigger(record, storage),
        record.data.cron_expression,
        name=record.data.name,
        job_id=record.id,
    )
    return CreateScheduleResponse(schedule_id=record.id)


@schedule_router.patch(
    "/{schedule_id}",
    response_model=ScheduleRecord,
    summary="Update a schedule",
)
async def update_schedule(
    schedule_id: str,
    body: UpdateScheduleRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    scheduler: SchedulerManager = Depends(get_scheduler_manager),
) -> ScheduleRecord:
    existing = await storage.get_schedule(user_id, schedule_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule '{schedule_id}' not found.",
        )

    updates = body.model_dump(exclude_none=True)
    updated_data = existing.data.model_copy(update=updates)
    updated_record = existing.model_copy(
        update={"data": updated_data, "updated_at": datetime.now()}
    )
    await storage.create_schedule(user_id, updated_record)

    try:
        scheduler.remove_task(schedule_id)
    except Exception:
        pass
    await scheduler.add_schedule(
        _build_trigger(updated_record, storage),
        updated_record.data.cron_expression,
        name=updated_record.data.name,
        job_id=updated_record.id,
    )
    return updated_record


@schedule_router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a schedule",
)
async def delete_schedule(
    schedule_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    scheduler: SchedulerManager = Depends(get_scheduler_manager),
) -> None:
    deleted = await storage.delete_schedule(user_id, schedule_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule '{schedule_id}' not found.",
        )
    try:
        scheduler.remove_task(schedule_id)
    except Exception:
        pass
