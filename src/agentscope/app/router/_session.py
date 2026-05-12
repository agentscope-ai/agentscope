# -*- coding: utf-8 -*-
"""Session router for managing agent sessions."""
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException  # noqa: F401
from pydantic import BaseModel, Field

session_router = APIRouter(
    prefix="/sessions",
    tags=["sessions"],
    responses={404: {"description": "Not found"}},
)


class SessionInfo(BaseModel):
    """Basic information of a session."""

    session_id: str = Field(
        description="The unique identifier of the session.",
    )
    name: str = Field(description="The display name of the session.")

    created_at: float = Field(
        description="The creation timestamp of the session (Unix epoch).",
    )
    updated_at: float = Field(
        description="The last-updated timestamp of the session (Unix epoch).",
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional extra metadata attached to the session.",
    )


class SessionListResponse(BaseModel):
    """Response model for listing sessions."""

    sessions: list[SessionInfo] = Field(
        description="The list of all sessions.",
    )
    total: int = Field(description="Total number of sessions.")


class CreateSessionRequest(BaseModel):
    """Request body for creating a new session."""

    name: str = Field(description="The display name for the new session.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional extra metadata to attach to the session.",
    )


class CreateSessionResponse(BaseModel):
    """Response model for a newly created session."""

    session_id: str = Field(
        description="The unique identifier of the created session.",
    )
    name: str = Field(description="The display name of the created session.")


class UpdateSessionRequest(BaseModel):
    """Request body for updating an existing session."""

    name: str | None = Field(
        default=None,
        description="New display name for the session. Omit to keep unchanged.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="New metadata for the session. Omit to keep unchanged.",
    )


class UpdateSessionResponse(BaseModel):
    """Response model after updating a session."""

    session_id: str = Field(description="The session identifier.")
    name: str = Field(description="The updated display name.")
    metadata: dict[str, Any] = Field(description="The updated metadata.")


@session_router.get(
    "/",
    response_model=SessionListResponse,
    summary="List all sessions",
)
async def list_sessions() -> SessionListResponse:
    """Return a list of all existing sessions.

    Returns:
        `SessionListResponse`:
            The list of sessions and the total count.
    """
    # TODO: query storage / session registry to retrieve all sessions
    sessions: list[SessionInfo] = []
    return SessionListResponse(sessions=sessions, total=len(sessions))


@session_router.post(
    "/",
    response_model=CreateSessionResponse,
    status_code=201,
    summary="Create a new session",
)
async def create_session(
    body: CreateSessionRequest,
) -> CreateSessionResponse:
    """Create a new session with the given name and optional metadata.

    Args:
        body (`CreateSessionRequest`):
            The request body containing session details.

    Returns:
        `CreateSessionResponse`:
            The identifier and name of the newly created session.
    """
    session_id = uuid.uuid4().hex
    # TODO: persist the new session to storage / session registry
    return CreateSessionResponse(session_id=session_id, name=body.name)


@session_router.delete(
    "/{session_id}",
    status_code=204,
    summary="Delete a session",
)
async def delete_session(session_id: str) -> None:
    """Delete a session and all associated agent states.

    Args:
        session_id (`str`):
            The unique identifier of the session to delete.

    Raises:
        `HTTPException`:
            404 if the session does not exist.
    """
    # TODO: verify session exists; raise HTTPException(404) if not found
    # TODO: remove all agent states linked to this session from storage
    # TODO: delete the session record from storage / session registry


@session_router.patch(
    "/{session_id}",
    response_model=UpdateSessionResponse,
    summary="Update a session",
)
async def update_session(
    session_id: str,
    body: UpdateSessionRequest,
) -> UpdateSessionResponse:
    """Update the name and/or metadata of an existing session.

    Args:
        session_id (`str`):
            The unique identifier of the session to update.
        body (`UpdateSessionRequest`):
            Fields to update; omit any field to leave it unchanged.

    Returns:
        `UpdateSessionResponse`:
            The session identifier together with its updated fields.

    Raises:
        `HTTPException`:
            404 if the session does not exist.
    """
    # TODO: load the existing session record; raise HTTPException(404) if not found
    # TODO: apply the partial updates and persist the changes to storage
    updated_name = body.name or ""
    updated_metadata = body.metadata or {}
    return UpdateSessionResponse(
        session_id=session_id,
        name=updated_name,
        metadata=updated_metadata,
    )
