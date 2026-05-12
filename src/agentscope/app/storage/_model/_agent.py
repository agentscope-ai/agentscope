# -*- coding: utf-8 -*-
"""The agent storage class."""
import uuid

from pydantic import BaseModel, Field

from ....agent import ContextConfig, ReActConfig


class AgentRecord(BaseModel):
    """The agent ORM model."""

    id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
    )
    """The user id."""

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
