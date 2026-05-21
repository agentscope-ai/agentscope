# -*- coding: utf-8 -*-
"""Session router — create, list, update, delete, and get messages."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .._deps import get_current_user_id, get_session_manager, get_storage
from .._manager import SessionManager
from .._schema import (
    CreateSessionRequest,
    CreateSessionResponse,
    ListMessagesResponse,
    ListSessionsResponse,
    UpdateSessionRequest,
)
from ..storage import StorageBase, SessionConfig, SessionRecord

session_router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    responses={404: {"description": "Not found"}},
)


@session_router.get(
    "/",
    response_model=ListSessionsResponse,
    summary="List sessions for an agent",
)
async def list_sessions(
    agent_id: str = Query(description="Filter sessions by agent ID."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> ListSessionsResponse:
    """Return all sessions belonging to the authenticated user for a given
    agent.

    Args:
        agent_id (`str`): Agent whose sessions to list.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `ListSessionsResponse`: All matching session records and their count.

    Raises:
        `HTTPException`: 404 if the agent does not exist or does not belong
            to the authenticated user.
    """
    agents = await storage.list_agents(user_id)
    if not any(a.id == agent_id for a in agents):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )

    sessions = await storage.list_sessions(user_id, agent_id)
    return ListSessionsResponse(sessions=sessions, total=len(sessions))


@session_router.post(
    "/",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new session",
)
async def create_session(
    body: CreateSessionRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> CreateSessionResponse:
    """Create (or resume) a session for a given agent and workspace.

    At most one session exists per ``(user_id, agent_id, workspace_id)``
    triple — a second call with the same triple updates the existing session
    rather than creating a duplicate.

    Args:
        body (`CreateSessionRequest`): Agent, workspace, and model config.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `CreateSessionResponse`: The session identifier.

    Raises:
        `HTTPException`: 404 if the agent or credential does not exist or
            does not belong to the authenticated user.
    """
    agents = await storage.list_agents(user_id)
    if not any(a.id == body.agent_id for a in agents):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{body.agent_id}' not found.",
        )

    if body.chat_model_config is not None:
        credentials = await storage.list_credentials(user_id)
        if not any(
            c.id == body.chat_model_config.credential_id for c in credentials
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Credential '{body.chat_model_config.credential_id}' "
                f"not found.",
            )

    session_record = await storage.upsert_session(
        user_id=user_id,
        agent_id=body.agent_id,
        config=SessionConfig(
            workspace_id=body.workspace_id or uuid.uuid4().hex,
            chat_model_config=body.chat_model_config,
            **({"name": body.name} if body.name is not None else {}),
        ),
    )
    return CreateSessionResponse(session_id=session_record.id)


@session_router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session",
)
async def delete_session(
    session_id: str,
    agent_id: str = Query(description="Agent the session belongs to."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> None:
    """Permanently delete a session and all its associated state.

    Args:
        session_id (`str`): The session to delete.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Raises:
        `HTTPException`: 404 if the session does not exist or does not belong
            to the authenticated user.
    """
    deleted = await storage.delete_session(user_id, agent_id, session_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )


@session_router.patch(
    "/{session_id}",
    response_model=SessionRecord,
    summary="Update a session",
)
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
    agent_id: str = Query(description="Agent the session belongs to."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> SessionRecord:
    """Update the model configuration of an existing session.

    Args:
        session_id (`str`): The session to update.
        body (`UpdateSessionRequest`): Fields to update.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `SessionRecord`: The full session record after the update.

    Raises:
        `HTTPException`: 404 if the session, agent, or credential does not
            exist or does not belong to the authenticated user.
    """
    existing = await storage.get_session(user_id, agent_id, session_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    if body.chat_model_config is not None:
        credentials = await storage.list_credentials(user_id)
        if not any(
            c.id == body.chat_model_config.credential_id for c in credentials
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Credential '{body.chat_model_config.credential_id}' "
                    f"not found."
                ),
            )

    updated_state = existing.state
    if body.permission_mode is not None:
        updated_ctx = existing.state.permission_context.model_copy(
            update={"mode": body.permission_mode},
        )

        updated_state = existing.state.model_copy(
            update={
                "permission_context": updated_ctx,
            },
        )

    config_updates = body.model_dump(
        exclude_none=True,
        exclude={"permission_mode"},
    )

    return await storage.upsert_session(
        user_id=user_id,
        agent_id=agent_id,
        config=existing.config.model_copy(
            update=dict(config_updates.items()),
        ),
        state=updated_state,
        session_id=session_id,
    )


# ----------------------------------------------------------------------
# Messages: fetch persisted messages for a session
# ----------------------------------------------------------------------


@session_router.get(
    "/{session_id}/messages",
    response_model=ListMessagesResponse,
    summary="List messages for a session",
)
async def list_messages(
    session_id: str,
    agent_id: str = Query(description="Agent the session belongs to."),
    offset: int = Query(0, ge=0, description="Pagination offset."),
    limit: int = Query(50, ge=1, le=200, description="Max messages."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    session_manager: SessionManager = Depends(get_session_manager),
) -> ListMessagesResponse:
    """Return persisted messages for a session.

    Args:
        session_id: The session to query.
        agent_id: Agent the session belongs to.
        offset: Pagination offset.
        limit: Maximum number of messages to return.
        user_id: Injected authenticated user ID.
        storage: Injected storage backend.
        session_manager: Injected session manager.

    Returns:
        Messages and running status.
    """
    existing = await storage.get_session(user_id, agent_id, session_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    messages = await storage.list_messages(
        user_id,
        session_id,
        offset=offset,
        limit=limit,
    )
    return ListMessagesResponse(
        messages=messages,
        is_running=session_manager.is_running(session_id),
    )
