# -*- coding: utf-8 -*-
"""Configuration models for realtime voice agents."""
from enum import StrEnum

from pydantic import BaseModel, Field


class TurnMode(StrEnum):
    """Who decides when a user turn begins and ends."""

    SERVER = "server"
    """The provider's VAD decides; the agent never commits turns."""

    HYBRID = "hybrid"
    """Local VAD drives immediate barge-in, while the provider still
    decides turn boundaries."""

    CLIENT = "client"
    """The agent decides, committing every turn explicitly."""


class RealtimeAgentConfig(BaseModel):
    """Pure configuration of a :class:`RealtimeAgent`."""

    turn_mode: TurnMode = TurnMode.SERVER

    fade_ms: int = Field(default=30, ge=0)
    """Fade-out applied when clearing playback, to avoid an audible click."""
