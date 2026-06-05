# -*- coding: utf-8 -*-
"""Shared FastAPI dependencies for the agentscope app."""
from fastapi import Header, HTTPException, Request, status

from .workspace_manager import WorkspaceManagerBase
from ._manager import (
    BackgroundTaskManager,
    ChatRunRegistry,
    SchedulerManager,
)
from ._service import ChatService, SessionService
from ._types import AgentMiddlewareFactory, AgentToolFactory
from .message_bus import MessageBus
from .storage import StorageBase


async def get_current_user_id(
    x_user_id: str = Header(
        description="Caller's user ID. "
        "Temporary header-based identity; will be replaced by JWT auth.",
    ),
) -> str:
    """Return the caller's user ID from the ``X-User-ID`` request header.

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


async def get_message_bus(request: Request) -> MessageBus:
    """Return the application-wide message bus.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `MessageBus`: The message bus instance stored in ``app.state``.
    """
    return request.app.state.message_bus


async def get_chat_service(request: Request) -> ChatService:
    """Return the application-wide chat service.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `ChatService`: The chat service instance stored in ``app.state``.
    """
    return request.app.state.chat_service


async def get_session_service(request: Request) -> SessionService:
    """Return the application-wide session service.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `SessionService`: The session service instance stored in
        ``app.state``.
    """
    return request.app.state.session_service


async def get_chat_run_registry(request: Request) -> ChatRunRegistry:
    """Return the per-process chat-run registry.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `ChatRunRegistry`: The registry stored in ``app.state``.
    """
    return request.app.state.chat_run_registry


async def get_scheduler_manager(request: Request) -> SchedulerManager:
    """Return the application-wide scheduler manager.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `SchedulerManager`: The scheduler manager stored in ``app.state``.
    """
    return request.app.state.scheduler_manager


async def get_background_task_manager(
    request: Request,
) -> BackgroundTaskManager:
    """Return the application-wide background task manager.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `BackgroundTaskManager`: The background task manager stored in
        ``app.state``.
    """
    return request.app.state.background_task_manager


async def get_workspace_manager(request: Request) -> WorkspaceManagerBase:
    """Return the application-wide workspace manager.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `WorkspaceManagerBase`: The workspace manager stored in ``app.state``.
    """
    return request.app.state.workspace_manager


async def get_extra_agent_middlewares(
    request: Request,
) -> AgentMiddlewareFactory | None:
    """Return the caller-supplied agent middleware factory, if any.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `AgentMiddlewareFactory | None`: The factory passed to
        :func:`~agentscope.app.create_app`, or ``None`` if not configured.
    """
    return request.app.state.extra_agent_middlewares


async def get_extra_agent_tools(
    request: Request,
) -> AgentToolFactory | None:
    """Return the caller-supplied agent tool factory, if any.

    Args:
        request (`Request`): The incoming FastAPI request.

    Returns:
        `AgentToolFactory | None`: The factory passed to
        :func:`~agentscope.app.create_app`, or ``None`` if not configured.
    """
    return request.app.state.extra_agent_tools
