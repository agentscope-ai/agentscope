# -*- coding: utf-8 -*-
"""Events emitted by a realtime model.

These are internal to :mod:`agentscope.realtime`: adapters normalise
vendor frames into them, and :class:`RealtimeAgent` translates them into
the public :class:`~agentscope.event.AgentEvent` stream.
"""
from pydantic import BaseModel, Field

from ..message import ToolCallBlock


class ModelEvent(BaseModel):
    """Common base class, for type hinting only."""


class SessionEndedEvent(ModelEvent):
    """The provider session is gone; no further events will arrive."""

    reason: str = ""


class SpeechStartedEvent(ModelEvent):
    """The provider's own VAD detected the user starting to speak."""

    item_id: str = ""
    at_ms: int = 0


class SpeechEndedEvent(ModelEvent):
    """The provider's own VAD detected the user stopping."""

    item_id: str = ""
    at_ms: int = 0


class InputTranscriptionEvent(ModelEvent):
    """The settled transcript of one user turn."""

    item_id: str = ""
    text: str


class ResponseCreatedEvent(ModelEvent):
    """The provider started producing a reply."""

    item_id: str


class AudioDeltaEvent(ModelEvent):
    """A chunk of assistant speech."""

    item_id: str
    pcm: bytes
    sample_rate: int


class TranscriptDeltaEvent(ModelEvent):
    """A chunk of the assistant's spoken text."""

    item_id: str
    delta: str


class ToolCallEvent(ModelEvent):
    """A complete tool call requested by the provider."""

    item_id: str
    tool_call: ToolCallBlock


class ResponseDoneEvent(ModelEvent):
    """The provider finished producing a reply."""

    item_id: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class ModelErrorEvent(ModelEvent):
    """An error reported by the provider."""

    code: str = ""
    message: str = ""
