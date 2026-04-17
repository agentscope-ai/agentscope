# -*- coding: utf-8 -*-
"""The agent state class."""
import uuid

from pydantic import BaseModel, Field

from ..message import TextBlock, DataBlock, Msg
from ..tool import PermissionContext


class AgentState(BaseModel):
    """The agent state that should be saved and loaded from storage."""

    summary: str | list[TextBlock | DataBlock] = ""
    """The compressed summary of the context, which will be prepended to the
    context when feed into the LLM."""
    context: list[Msg] = []
    """The uncompressed conversation context, that will be feed into the LLM"""
    reply_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The id of the current reply, which is also used as the id of the
    final message of the reply."""
    cur_iter: int = 0
    """The current iteration of the agent's reasoning-acting loop."""

    # The tool state, e.g. the active tool groups
    activated_groups: list[str] = []
    """The names of the activated tool groups, each group contains a set of
    tools."""

    permissionContext: PermissionContext = Field(
        default_factory=PermissionContext,
    )
    """The permission context that will be passed to the toolkit to determine
    the tool permissions."""
