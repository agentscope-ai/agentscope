# -*- coding: utf-8 -*-
""""""
from typing import Any

from pydantic import BaseModel, Field

from ...agent import CompressionConfig, ReActConfig


class _ChatModelConfig(BaseModel):
    """The model configuration model."""

    credential_id: str = Field(
        description="ID of the credential holding the API key for this model.",
    )
    model: str = Field(description="Model name, e.g. ``gpt-4o``.")
    max_retries: int = Field(
        default=10,
        gt=0,
        description="Maximum number of retries when the model call fails.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra provider-specific parameters passed to the model.",
    )


class AgentConfig(BaseModel):
    """The agent configuration stored in the database."""

    name: str = Field(description="Display name of the agent.")
    system_prompt: str = Field(
        default="You're a helpful assistant.",
        description="Base system prompt fed to the agent.",
    )
    chat_model_config: _ChatModelConfig = Field(
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
