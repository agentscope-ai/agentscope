# -*- coding: utf-8 -*-
"""The agent configuration model."""
from pydantic import BaseModel, Field

from ._chat_model import ChatModelConfig
from ...agent import CompressionConfig, ReActConfig


class AgentConfig(BaseModel):
    """The agent configuration stored in the database."""

    name: str = Field(description="Display name of the agent.")
    system_prompt: str = Field(
        default="You're a helpful assistant.",
        description="Base system prompt fed to the agent.",
    )
    chat_model_config: ChatModelConfig = Field(
        description="Configuration of the language model used by the agent.",
    )
    compression_config: CompressionConfig = Field(
        default_factory=CompressionConfig,
        description="Context-compression configuration.",
    )
    react_config: ReActConfig = Field(
        default_factory=ReActConfig,
        description="ReAct loop configuration (max iterations, parallelism).",
    )
