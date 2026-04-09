# -*- coding: utf-8 -*-
"""The thinking config in AgentScope model module."""
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ThinkingConfig(BaseModel):
    """The thinking config for AgentScope chat models."""
    model_config = ConfigDict(extra="allow")
    """Allow extra fields in the thinking config."""

    enable: bool
    """If enable the thinking capabilities."""

    effort: Literal["max", "high", "medium", "low"]
    """The thinking effort level"""


class GenerateConfig(BaseModel):
    """The generate config for AgentScope chat models."""
    model_config = ConfigDict(extra="allow")
    """Allow extra fields in the generate config."""

    temperature: float | None = None
    """The temperature for response generation."""

    top_p: float | None = None
    """The nucleus sampling probability for response generation."""