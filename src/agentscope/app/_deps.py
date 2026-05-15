# -*- coding: utf-8 -*-
"""Shared FastAPI dependencies for the agentscope app."""
from fastapi import Header, HTTPException, Request, status

from ._manager import SessionManager
from ._manager._scheduler import SchedulerManager
from .storage import StorageBase


async def get_current_user_id(
    x_user_id: str = Header(
        description="Caller's user ID. "
        "Temporary header-based identity; will be replaced by JWT auth.",
    ),
) -> str:
    """Return the caller's user ID from the ``X-User-ID`` request header.

    This is a placeholder dependency. Once an auth middleware is in place,
    replace the header extraction with a JWT / session-token lookup and remove
    the ``X-User-ID`` header entirely.

    Args:
        x_user_id (`str`): Value of the ``X-User-ID`` header.

    Returns:
        `str`: The authenticated user ID.

    Raises:
        `HTTPException`: 401 if the header is missing or empty.
    """
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-ID header is required.",
        )
    return x_user_id


async def get_storage(request: Request) -> StorageBase:
    """Return the application-wide storage backend.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `StorageBase`: The storage instance stored in ``app.state``.
    """
    return request.app.state.storage


async def get_session_manager(request: Request) -> SessionManager:
    """Return the application-wide session manager.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `SessionManager`: The session manager stored in ``app.state``.
    """
    return request.app.state.session_manager


async def get_scheduler_manager(request: Request) -> SchedulerManager:
    """Return the application-wide scheduler manager.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `SchedulerManager`: The scheduler manager stored in ``app.state``.
    """
    return request.app.state.scheduler_manager
