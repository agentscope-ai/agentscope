# -*- coding: utf-8 -*-
"""Event definitions for live voice agent.

ModelEvent: API events from model layer (consumed by agent)
AgentEvent: Backend to Web events (dispatched to other agents/frontend)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# =============================================================================
# Model Events - API 事件（Model 层产出，Agent 消费）
# =============================================================================


class ModelEventType(str, Enum):
    """Types of model events from the API."""

    # Session lifecycle
    SESSION_CREATED = "session_created"
    SESSION_UPDATED = "session_updated"

    # Response events
    RESPONSE_CREATED = "response_created"
    RESPONSE_AUDIO_DELTA = "response_audio_delta"
    RESPONSE_AUDIO_DONE = "response_audio_done"
    RESPONSE_AUDIO_TRANSCRIPT_DELTA = "response_audio_transcript_delta"
    RESPONSE_AUDIO_TRANSCRIPT_DONE = "response_audio_transcript_done"
    RESPONSE_TOOL_USE_DELTA = "response_tool_use_delta"
    RESPONSE_TOOL_USE_DONE = "response_tool_use_done"
    RESPONSE_DONE = "response_done"

    # Input transcription
    INPUT_TRANSCRIPTION_DELTA = "input_transcription_delta"
    INPUT_TRANSCRIPTION_DONE = "input_transcription_done"

    # Input detection (VAD)
    INPUT_STARTED = "input_started"
    INPUT_DONE = "input_done"

    # Error
    ERROR = "error"

    # WebSocket connection state
    WEBSOCKET_CONNECT = "websocket_connect"
    WEBSOCKET_DISCONNECT = "websocket_disconnect"


@dataclass
class ModelEvent:
    """Base class for model events (API layer events)."""

    type: ModelEventType


@dataclass
class ModelSessionCreated(ModelEvent):
    """Session created event."""

    session_id: str
    type: ModelEventType = field(
        default=ModelEventType.SESSION_CREATED,
        init=False,
    )


@dataclass
class ModelSessionUpdated(ModelEvent):
    """Session updated event."""

    session_id: str
    type: ModelEventType = field(
        default=ModelEventType.SESSION_UPDATED,
        init=False,
    )


@dataclass
class ModelResponseCreated(ModelEvent):
    """Response created event."""

    response_id: str
    type: ModelEventType = field(
        default=ModelEventType.RESPONSE_CREATED,
        init=False,
    )


@dataclass
class ModelResponseAudioDelta(ModelEvent):
    """Response audio delta event."""

    response_id: str
    delta: str  # base64 encoded audio data
    item_id: str | None = None
    content_index: int | None = None
    output_index: int | None = None
    type: ModelEventType = field(
        default=ModelEventType.RESPONSE_AUDIO_DELTA,
        init=False,
    )


@dataclass
class ModelResponseAudioDone(ModelEvent):
    """Response audio done event."""

    response_id: str
    item_id: str | None = None
    content_index: int | None = None
    output_index: int | None = None
    type: ModelEventType = field(
        default=ModelEventType.RESPONSE_AUDIO_DONE,
        init=False,
    )


@dataclass
class ModelResponseAudioTranscriptDelta(ModelEvent):
    """Response audio transcript delta event."""

    response_id: str
    delta: str  # text delta
    item_id: str | None = None
    content_index: int | None = None
    output_index: int | None = None
    type: ModelEventType = field(
        default=ModelEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA,
        init=False,
    )


@dataclass
class ModelResponseAudioTranscriptDone(ModelEvent):
    """Response audio transcript done event."""

    response_id: str
    item_id: str | None = None
    content_index: int | None = None
    output_index: int | None = None
    type: ModelEventType = field(
        default=ModelEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE,
        init=False,
    )


@dataclass
class ModelResponseToolUseDelta(ModelEvent):
    """Response tool use delta event."""

    response_id: str
    call_id: str
    delta: str  # argument JSON string delta
    item_id: str | None = None
    output_index: int | None = None
    name: str | None = None
    type: ModelEventType = field(
        default=ModelEventType.RESPONSE_TOOL_USE_DELTA,
        init=False,
    )


@dataclass
class ModelResponseToolUseDone(ModelEvent):
    """Response tool use done event."""

    response_id: str
    call_id: str
    item_id: str | None = None
    output_index: int | None = None
    type: ModelEventType = field(
        default=ModelEventType.RESPONSE_TOOL_USE_DONE,
        init=False,
    )


@dataclass
class ModelResponseDone(ModelEvent):
    """Response done event."""

    response_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    type: ModelEventType = field(
        default=ModelEventType.RESPONSE_DONE,
        init=False,
    )


@dataclass
class ModelInputTranscriptionDelta(ModelEvent):
    """Input transcription delta event."""

    delta: str
    content_index: int | None = None
    item_id: str | None = None
    type: ModelEventType = field(
        default=ModelEventType.INPUT_TRANSCRIPTION_DELTA,
        init=False,
    )


@dataclass
class ModelInputTranscriptionDone(ModelEvent):
    """Input transcription done event."""

    transcript: str
    input_tokens: int = 0
    output_tokens: int = 0
    item_id: str | None = None
    type: ModelEventType = field(
        default=ModelEventType.INPUT_TRANSCRIPTION_DONE,
        init=False,
    )


@dataclass
class ModelInputStarted(ModelEvent):
    """Input started event (VAD detected speech start)."""

    item_id: str
    audio_start_ms: int
    type: ModelEventType = field(
        default=ModelEventType.INPUT_STARTED,
        init=False,
    )


@dataclass
class ModelInputDone(ModelEvent):
    """Input done event (VAD detected speech end)."""

    item_id: str
    audio_end_ms: int
    type: ModelEventType = field(default=ModelEventType.INPUT_DONE, init=False)


@dataclass
class ModelError(ModelEvent):
    """Error event."""

    error_type: str
    code: str
    message: str
    type: ModelEventType = field(default=ModelEventType.ERROR, init=False)


@dataclass
class ModelWebSocketConnect(ModelEvent):
    """WebSocket connected event."""

    type: ModelEventType = field(
        default=ModelEventType.WEBSOCKET_CONNECT,
        init=False,
    )


@dataclass
class ModelWebSocketDisconnect(ModelEvent):
    """WebSocket disconnected event."""

    type: ModelEventType = field(
        default=ModelEventType.WEBSOCKET_DISCONNECT,
        init=False,
    )


# =============================================================================
# Agent Events - 后端到 Web 的事件（Agent 产出，分发到其他 Agents/前端）
# =============================================================================


class AgentEventType(str, Enum):
    """Types of agent events for backend-to-web communication."""

    # Session lifecycle
    SESSION_CREATED = "session_created"
    SESSION_UPDATED = "session_updated"

    # Response events
    RESPONSE_CREATED = "response_created"
    RESPONSE_DELTA = "response_delta"
    RESPONSE_DONE = "response_done"

    # Input transcription
    INPUT_TRANSCRIPTION_DELTA = "input_transcription_delta"
    INPUT_TRANSCRIPTION_DONE = "input_transcription_done"

    # Input detection
    INPUT_STARTED = "input_started"
    INPUT_DONE = "input_done"

    # Error
    ERROR = "error"


# Content block types for agent events
@dataclass
class TextBlock:
    """Text content block."""

    text: str
    type: str = field(default="text", init=False)


@dataclass
class AudioBlock:
    """Audio content block."""

    data: str  # base64 encoded audio data
    media_type: str = "audio/pcm;rate=24000"
    type: str = field(default="audio", init=False)


@dataclass
class ToolUseBlock:
    """Tool use content block."""

    id: str
    name: str
    input: dict[str, Any]
    type: str = field(default="tool_use", init=False)


@dataclass
class ToolResultBlock:
    """Tool result content block."""

    id: str
    name: str
    output: str
    type: str = field(default="tool_result", init=False)


# Union type for content blocks
ContentBlock = TextBlock | AudioBlock | ToolUseBlock | ToolResultBlock


@dataclass
class AgentEvent:
    """Base class for agent events (backend-to-web events)."""

    type: AgentEventType
    agent_id: str
    agent_name: str


@dataclass
class AgentSessionCreated(AgentEvent):
    """Session created event."""

    session_id: str
    type: AgentEventType = field(
        default=AgentEventType.SESSION_CREATED,
        init=False,
    )


@dataclass
class AgentSessionUpdated(AgentEvent):
    """Session updated event."""

    session_id: str
    type: AgentEventType = field(
        default=AgentEventType.SESSION_UPDATED,
        init=False,
    )


@dataclass
class AgentResponseCreated(AgentEvent):
    """Response created event."""

    response_id: str
    type: AgentEventType = field(
        default=AgentEventType.RESPONSE_CREATED,
        init=False,
    )


@dataclass
class AgentResponseDelta(AgentEvent):
    """Response delta event with content block."""

    response_id: str
    delta: ContentBlock
    type: AgentEventType = field(
        default=AgentEventType.RESPONSE_DELTA,
        init=False,
    )


@dataclass
class AgentResponseDone(AgentEvent):
    """Response done event."""

    response_id: str
    type: AgentEventType = field(
        default=AgentEventType.RESPONSE_DONE,
        init=False,
    )


@dataclass
class AgentInputTranscriptionDelta(AgentEvent):
    """Input transcription delta event."""

    delta: str
    item_id: str
    content_index: int = 0
    type: AgentEventType = field(
        default=AgentEventType.INPUT_TRANSCRIPTION_DELTA,
        init=False,
    )


@dataclass
class AgentInputTranscriptionDone(AgentEvent):
    """Input transcription done event."""

    transcription: str
    item_id: str
    type: AgentEventType = field(
        default=AgentEventType.INPUT_TRANSCRIPTION_DONE,
        init=False,
    )


@dataclass
class AgentInputStarted(AgentEvent):
    """Input started event."""

    item_id: str
    audio_start_ms: int
    type: AgentEventType = field(
        default=AgentEventType.INPUT_STARTED,
        init=False,
    )


@dataclass
class AgentInputDone(AgentEvent):
    """Input done event."""

    item_id: str
    audio_end_ms: int
    type: AgentEventType = field(default=AgentEventType.INPUT_DONE, init=False)


@dataclass
class AgentError(AgentEvent):
    """Error event."""

    error_type: str
    code: str
    message: str
    type: AgentEventType = field(default=AgentEventType.ERROR, init=False)


# =============================================================================
# Type aliases for convenience
# =============================================================================

# All model event types
ModelEventUnion = (
    ModelSessionCreated
    | ModelSessionUpdated
    | ModelResponseCreated
    | ModelResponseAudioDelta
    | ModelResponseAudioDone
    | ModelResponseAudioTranscriptDelta
    | ModelResponseAudioTranscriptDone
    | ModelResponseToolUseDelta
    | ModelResponseToolUseDone
    | ModelResponseDone
    | ModelInputTranscriptionDelta
    | ModelInputTranscriptionDone
    | ModelInputStarted
    | ModelInputDone
    | ModelError
    | ModelWebSocketConnect
    | ModelWebSocketDisconnect
)

# All agent event types
AgentEventUnion = (
    AgentSessionCreated
    | AgentSessionUpdated
    | AgentResponseCreated
    | AgentResponseDelta
    | AgentResponseDone
    | AgentInputTranscriptionDelta
    | AgentInputTranscriptionDone
    | AgentInputStarted
    | AgentInputDone
    | AgentError
)
