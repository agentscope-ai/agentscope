# -*- coding: utf-8 -*-
"""The chat model configuration, used as DTO layer."""
from typing import Any

from pydantic import BaseModel, Field

from ...agent import ReActConfig


class ChatModelConfig(BaseModel):
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
