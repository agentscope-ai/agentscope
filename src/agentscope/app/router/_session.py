# -*- coding: utf-8 -*-
"""Session router — create, list, update, and delete sessions."""
from fastapi import APIRouter, Depends, HTTPException, Query, status

from .._deps import get_current_user_id, get_storage
from .._schema._session import (
    CreateSessionRequest,
    CreateSessionResponse,
    SessionListResponse,
    UpdateSessionRequest,
)
from ..storage._base import StorageBase
from ..storage._model._session import SessionData

session_router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    responses={404: {"description": "Not found"}},
)


@session_router.get(
    "/",
    response_model=SessionListResponse,
    summary="List sessions for an agent",
)
async def list_sessions(
    agent_id: str = Query(description="Filter sessions by agent ID."),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> SessionListResponse:
    """Return all sessions belonging to the authenticated user for a given agent.

    Args:
        agent_id (`str`): Agent whose sessions to list.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `SessionListResponse`: All matching session records and their count.

    Raises:
        `HTTPException`: 404 if the agent does not exist or does not belong
            to the authenticated user.
    """
    agents = await storage.list_agent(user_id)
    if not any(a.id == agent_id for a in agents):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )

    sessions = await storage.list_sessions(user_id, agent_id)
    return SessionListResponse(sessions=sessions, total=len(sessions))


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
    agents = await storage.list_agent(user_id)
    if not any(a.id == body.agent_id for a in agents):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{body.agent_id}' not found.",
        )

    credentials = await storage.list_credentials(user_id)
    if not any(c.id == body.chat_model_config.credential_id for c in credentials):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Credential '{body.chat_model_config.credential_id}' not found.",
        )

    from ....state import AgentState  # avoid circular at module level

    await storage.upsert_session(
        user_id=user_id,
        agent_id=body.agent_id,
        workspace_id=body.workspace_id,
        session_data=SessionData(
            agent_state=AgentState(),
            chat_model_config=body.chat_model_config,
        ),
    )

    sessions = await storage.list_sessions(user_id, body.agent_id)
    session = next(
        s for s in sessions if s.workspace_id == body.workspace_id
    )
    return CreateSessionResponse(session_id=session.id)


@session_router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session",
)
async def delete_session(
    session_id: str,
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
    deleted = await storage.delete_session(user_id, session_id)
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
    existing = await storage.get_session(user_id, session_id)
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
                detail=f"Credential '{body.chat_model_config.credential_id}' not found.",
            )

    updated_data = existing.data.model_copy(
        update={
            k: v
            for k, v in body.model_dump(exclude_none=True).items()
        }
    )
    await storage.upsert_session(
        user_id=user_id,
        agent_id=existing.agent_id,
        workspace_id=existing.workspace_id,
        session_data=updated_data,
    )
    return await storage.get_session(user_id, session_id)
