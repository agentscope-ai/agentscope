# -*- coding: utf-8 -*-
"""The session data class for storage."""
from pydantic import BaseModel

from ._base import _RecordBase
from ....state import AgentState


class ChatModelConfig(BaseModel):
    """The model configuration class."""

    type: str
    """The provider type."""

    credential_id: str
    """The credential id."""

    parameters: dict
    """The model parameters."""


class SessionData(BaseModel):
    """The session data class."""

    agent_state: AgentState
    """The agent state."""

    chat_model_config: ChatModelConfig
    """The chat model config."""


class SessionRecord(_RecordBase):
    """The session record."""

    user_id: str
    """The user id."""

    agent_id: str
    """The agent id."""

    workspace_id: str
    """The workspace id."""

    data: SessionData
    """The session data."""
