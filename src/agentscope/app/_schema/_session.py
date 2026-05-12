# -*- coding: utf-8 -*-
"""Request / response schemas for the session router."""
from pydantic import BaseModel, Field

from ..storage._model._session import ChatModelConfig, SessionRecord


class CreateSessionRequest(BaseModel):
    """Request body for creating a new session."""

    agent_id: str = Field(description="Agent this session belongs to.")
    workspace_id: str = Field(description="Workspace this session belongs to.")
    chat_model_config: ChatModelConfig = Field(
        description="Model provider and parameters for this session.",
    )


class CreateSessionResponse(BaseModel):
    """Response body after creating a session."""

    session_id: str = Field(description="Server-assigned session identifier.")


class UpdateSessionRequest(BaseModel):
    """Request body for updating an existing session.

    Omit any field to keep its current value.
    """

    chat_model_config: ChatModelConfig | None = Field(
        default=None,
        description="New model configuration. Replaces the existing one entirely.",
    )


class SessionListResponse(BaseModel):
    """Response body for listing sessions."""

    sessions: list[SessionRecord] = Field(description="Session records.")
    total: int = Field(description="Total number of sessions.")
