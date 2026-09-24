# -*- coding: utf-8 -*-
"""The a2a agent state."""

from pydantic import BaseModel, Field

from ..message import Msg
from .._utils._common import _generate_id


class A2AAgentState(BaseModel):
    """The A2A agent state."""

    session_id: str = Field(default_factory=_generate_id)
    """The session id of the agent, used to group its streamed events."""

    context_id: str | None = None
    """The context_id of the A2A protocol."""

    task_id: str | None = None
    """The current task id of the agent."""

    observed_context: list[Msg] = Field(default_factory=list)
    """The observed context of the agent."""
