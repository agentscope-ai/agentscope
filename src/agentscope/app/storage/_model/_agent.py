# -*- coding: utf-8 -*-
"""The agent storage class."""

from pydantic import Field, BaseModel

from ._base import _RecordBase
from ....agent import ContextConfig, ReActConfig


class AgentData(BaseModel):
    """The agent data model."""

    id: str = Field(
        description="Unique agent id",
    )
    """The agent id."""

    name: str = Field(
        description="The name of the agent.",
        title="Name",
    )

    system_prompt: str = Field(
        description="The system prompt for the agent.",
        title="System Prompt",
    )

    context_config: ContextConfig = Field(
        description="The context config for the agent.",
        title="Context Config",
    )

    react_config: ReActConfig = Field(
        description="The react config for the agent.",
        title="React Config",
    )


class AgentRecord(_RecordBase):
    """The agent ORM model."""

    user_id: str
    """The user id"""

    data: AgentData
    """The agent data"""
