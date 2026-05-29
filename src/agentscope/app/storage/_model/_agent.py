# -*- coding: utf-8 -*-
"""The agent storage class."""
import uuid

from pydantic import Field, BaseModel

from ._base import _RecordBase
from ....agent import ContextConfig, ReActConfig


class AgentData(BaseModel):
    """The agent data model."""

    id: str = Field(
        description="Unique agent id",
        default_factory=lambda: uuid.uuid4().hex,
    )
    """The agent id."""

    name: str = Field(
        description="The name of the agent.",
        title="Name",
    )

    system_prompt: str = Field(
        default="You're a helpful assistant.",
        description="The system prompt for the agent.",
        title="System Prompt",
        # Hint for schema-driven UI renderers; see ``ContextConfig`` for
        # the same pattern on long-form prompts.
        json_schema_extra={"format": "textarea"},
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
