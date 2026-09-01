# -*- coding: utf-8 -*-
"""Schedule router — CRUD endpoints for scheduled agent tasks."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status

from ..access import ResourceKind
from .._manager import SchedulerManager
from ..deps import (
    get_current_user_id,
    get_resource_access_service,
    get_scheduler_manager,
    get_session_service,
    get_storage,
)
from ._schema import (
    CreateScheduleRequest,
    CreateScheduleResponse,
    ListSchedulesResponse,
    ScheduleSessionsResponse,
    UpdateScheduleRequest,
)
from .._service import ResourceAccessService, SessionService
from ..storage import (
    StorageBase,
    ScheduleData,
    ScheduleRecord,
    ScheduleSource,
)

schedule_router = APIRouter(
    prefix="/schedule",
    tags=["schedule"],
    responses={404: {"description": "Not found"}},
)


@schedule_router.get(
    "/",
    response_model=ListSchedulesResponse,
    summary="List all schedules",
)
async def list_schedules(
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> ListSchedulesResponse:
    """List all schedules owned by the current user.

    Args:
        user_id (`str`): Authenticated user ID.
        storage (`StorageBase`): Storage instance.

    Returns:
        `ListSchedulesResponse`:
            Paginated list of schedule records.
    """
    schedules = await storage.list_schedules(user_id)
    return ListSchedulesResponse(schedules=schedules, total=len(schedules))


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
    access: ResourceAccessService = Depends(get_resource_access_service),
    scheduler: SchedulerManager = Depends(get_scheduler_manager),
) -> CreateScheduleResponse:
    """Create a new schedule and register it with the scheduler.

    The referenced agent may be either the viewer's own or one shared
    to them through :class:`ResourceAccessPolicyBase`; the schedule
    record itself is always owned by the caller.

    Args:
        body (`CreateScheduleRequest`): Schedule configuration.
        user_id (`str`): Authenticated user ID.
        storage (`StorageBase`): Storage instance.
        access (`ResourceAccessService`): Access service.
        scheduler (`SchedulerManager`): Scheduler manager.

    Returns:
        `CreateScheduleResponse`:
            The ID of the newly created schedule.

    Raises:
        `HTTPException`: 404 if the specified agent or the credential
            referenced by ``chat_model_config`` is not visible to the
            caller.
    """
    # Visibility checks — raise 404 when neither owned nor shared. The
    # schedule fires under the owner's user_id, so re-validating the
    # credential here surfaces the error at creation time rather than
    # silently at the first (possibly much later) scheduled run.
    await access.resolve_agent(user_id, body.agent_id)
    await access.get_resource(
        user_id,
        ResourceKind.CREDENTIAL,
        body.chat_model_config.credential_id,
    )

    record = ScheduleRecord(
        user_id=user_id,
        agent_id=body.agent_id,
        data=ScheduleData(
            name=body.name,
            description=body.description,
            cron_expression=body.cron_expression,
            timezone=body.timezone,
            enabled=body.enabled,
            stateful=body.stateful,
            permission_mode=body.permission_mode,
            chat_model_config=body.chat_model_config,
            source=ScheduleSource.USER,
            started_at=datetime.now(),
        ),
    )
    await storage.upsert_schedule(user_id, record)

    if record.data.enabled:
        await scheduler.register_schedule(record)

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
    """Partially update a schedule.

    Fields omitted from the request body keep their current values.
    Changing ``cron_expression`` or ``timezone`` immediately reschedules the
    APScheduler job.  Setting ``enable=False`` removes the job from the
    scheduler without deleting the record.

    Args:
        schedule_id (`str`): ID of the schedule to update.
        body (`UpdateScheduleRequest`): Fields to update.
        user_id (`str`): Authenticated user ID.
        storage (`StorageBase`): Storage instance.
        scheduler (`SchedulerManager`): Scheduler manager.

    Returns:
        `ScheduleRecord`:
            The updated schedule record.

    Raises:
        `HTTPException`: 404 if the schedule does not exist.
    """
    existing = await storage.get_schedule(user_id, schedule_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule '{schedule_id}' not found.",
        )

    updates = body.model_dump(exclude_none=True)
    updated_data = existing.data.model_copy(update=updates)
    updated_record = existing.model_copy(
        update={"data": updated_data, "updated_at": datetime.now()},
    )
    await storage.upsert_schedule(user_id, updated_record)

    # Always remove the existing job first; re-register only if still enabled.
    await scheduler.remove_schedule(schedule_id)
    if updated_record.data.enabled:
        await scheduler.register_schedule(updated_record)

    return updated_record


@schedule_router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a schedule",
)
async def delete_schedule(
    schedule_id: str,
    user_id: str = Depends(get_current_user_id),
    session_service: SessionService = Depends(get_session_service),
    scheduler: SchedulerManager = Depends(get_scheduler_manager),
) -> None:
    """Permanently delete a schedule.

    Cancels any in-flight chat run for sessions this schedule has
    triggered, removes their records via the session service, and
    finally unregisters the APScheduler job.

    Args:
        schedule_id (`str`): ID of the schedule to delete.
        user_id (`str`): Authenticated user ID.
        session_service (`SessionService`): Injected session service.
        scheduler (`SchedulerManager`): Scheduler manager.

    Raises:
        `HTTPException`: 404 if the schedule does not exist.
    """
    deleted = await session_service.delete_schedule(user_id, schedule_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule '{schedule_id}' not found.",
        )
    await scheduler.remove_schedule(schedule_id)


@schedule_router.get(
    "/{schedule_id}/sessions",
    response_model=ScheduleSessionsResponse,
    summary="List execution sessions for a schedule",
)
async def list_schedule_sessions(
    schedule_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> ScheduleSessionsResponse:
    """Return all sessions triggered by a given schedule.

    Args:
        schedule_id (`str`): ID of the schedule.
        user_id (`str`): Authenticated user ID.
        storage (`StorageBase`): Storage instance.

    Returns:
        `ScheduleSessionsResponse`:
            List of execution sessions ordered by creation time (newest first).

    Raises:
        `HTTPException`: 404 if the schedule does not exist.
    """
    existing = await storage.get_schedule(user_id, schedule_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule '{schedule_id}' not found.",
        )

    sessions = await storage.list_sessions_by_schedule(user_id, schedule_id)
    return ScheduleSessionsResponse(sessions=sessions, total=len(sessions))
