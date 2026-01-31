# -*- coding: utf-8 -*-
"""The unified event from realtime model APIs in AgentScope, which will be
consumed by the realtime agents."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class ModelEventType(str, Enum):
    """Types of model events from the API."""

    # Session lifecycle
    SESSION_CREATED = "model.session.created"
    SESSION_ENDED = "model.session.ended"

    # Response events
    RESPONSE_CREATED = "model.response.created"
    RESPONSE_CANCELLED = "model.response.cancelled"

    RESPONSE_AUDIO_DELTA = "model.response.audio.delta"
    RESPONSE_AUDIO_DONE = "model.response.audio.done"
    RESPONSE_AUDIO_TRANSCRIPT_DELTA = "model.response.audio_transcript.delta"
    RESPONSE_AUDIO_TRANSCRIPT_DONE = "model.response_audio.transcript.done"
    RESPONSE_TOOL_USE_DELTA = "model.response.tool_use.delta"
    RESPONSE_TOOL_USE_DONE = "model.response.tool_use.done"
    RESPONSE_DONE = "model.response.done"

    # Input transcription
    INPUT_TRANSCRIPTION_DELTA = "model.input_transcription.delta"
    INPUT_TRANSCRIPTION_DONE = "model.input_transcription.done"

    # Input detection (VAD)
    INPUT_STARTED = "model.input.started"
    INPUT_DONE = "model.input.done"

    # Error
    ERROR = "model.error"

    # WebSocket events (if used)
    WEBSOCKET_CONNECT = "model.websocket_connect"
    WEBSOCKET_DISCONNECT = "model.websocket_disconnect"


class ModelEvents:
    """The realtime model events that will be consumed by the realtime
    agents"""

    class EventBase:
        """The base class for all model events, used to unify the type
        hinting."""

    @dataclass
    class SessionCreatedEvent(EventBase):
        """Session created event."""

        session_id: str
        """The session ID."""

        type: Literal[
            ModelEventType.SESSION_CREATED
        ] = ModelEventType.SESSION_CREATED
        """The event type."""

    @dataclass
    class SessionEndedEvent(EventBase):
        """Session ended event."""

        session_id: str
        """The session ID."""

        reason: str
        """The reason for session end."""

        type: Literal[ModelEventType.SESSION_ENDED]
        """The event type."""

    @dataclass
    class ResponseCreatedEvent(EventBase):
        """The realtime model begins generating a response."""

        response_id: str
        """The response ID."""

        type: Literal[
            ModelEventType.RESPONSE_CREATED
        ] = ModelEventType.RESPONSE_CREATED
        """The event type."""

    @dataclass
    class ResponseDoneEvent(EventBase):
        """Model response done event."""

        response_id: str
        """The response ID."""

        input_tokens: int
        """The number of input tokens."""

        output_tokens: int
        """The number of output tokens."""

        metadata: dict[str, str] = field(default_factory=dict)
        """Additional metadata."""

        type: Literal[
            ModelEventType.RESPONSE_DONE
        ] = ModelEventType.RESPONSE_DONE
        """The event type."""

    @dataclass
    class ResponseAudioDeltaEvent(EventBase):
        """Model response audio delta event."""

        response_id: str
        """The response ID."""

        item_id: str
        """The conversation item ID."""

        delta: str
        """The audio chunk data, encoded in base64."""

        format: dict
        """The audio format information."""

        type: Literal[
            ModelEventType.RESPONSE_AUDIO_DELTA
        ] = ModelEventType.RESPONSE_AUDIO_DELTA
        """The event type."""

    @dataclass
    class ResponseAudioDoneEvent(EventBase):
        """Model response audio done event."""

        response_id: str
        """The response ID."""

        item_id: str
        """The conversation item ID."""

        type: Literal[
            ModelEventType.RESPONSE_AUDIO_DONE
        ] = ModelEventType.RESPONSE_AUDIO_DONE
        """The event type."""

    @dataclass
    class ResponseAudioTranscriptDeltaEvent(EventBase):
        """Model response audio transcript delta event."""

        response_id: str
        """The response ID."""

        item_id: str
        """The conversation item ID."""

        delta: str
        """The transcript chunk data."""

        type: Literal[
            ModelEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA
        ] = ModelEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA
        """The event type."""

    @dataclass
    class ResponseAudioTranscriptDoneEvent(EventBase):
        """Model response audio transcript done event."""

        response_id: str
        """The response ID."""

        item_id: str
        """The conversation item ID."""

        type: Literal[
            ModelEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE
        ] = ModelEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE
        """The event type."""

    @dataclass
    class ResponseToolUseDeltaEvent(EventBase):
        """Model response tool use delta event."""

        response_id: str
        """The response ID."""

        item_id: str
        """The response item ID."""

        name: str
        """The tool name."""

        call_id: str
        """The tool call ID."""

        input: str  # accumulated tool arguments JSON string
        """The accumulated tool arguments as JSON string."""

        type: Literal[
            ModelEventType.RESPONSE_TOOL_USE_DELTA
        ] = ModelEventType.RESPONSE_TOOL_USE_DELTA
        """The event type."""

    @dataclass
    class ResponseToolUseDoneEvent(EventBase):
        """Model response tool use done event."""

        response_id: str
        """The response ID."""

        item_id: str
        """The response item ID."""

        call_id: str
        """The tool call ID."""

        type: Literal[
            ModelEventType.RESPONSE_TOOL_USE_DONE
        ] = ModelEventType.RESPONSE_TOOL_USE_DONE
        """The event type."""

    @dataclass
    class InputTranscriptionDeltaEvent(EventBase):
        """Input transcription delta event."""

        item_id: str
        """The conversation item ID."""

        delta: str
        """The transcription delta."""

        type: Literal[
            ModelEventType.INPUT_TRANSCRIPTION_DELTA
        ] = ModelEventType.INPUT_TRANSCRIPTION_DELTA
        """The event type."""

    @dataclass
    class InputTranscriptionDoneEvent(EventBase):
        """Input transcription done event."""

        item_id: str
        """The conversation item ID."""

        transcript: str
        """The complete transcription."""

        input_tokens: int | None = None
        """The number of input tokens."""

        output_tokens: int | None = None
        """The number of output tokens."""

        type: Literal[
            ModelEventType.INPUT_TRANSCRIPTION_DONE
        ] = ModelEventType.INPUT_TRANSCRIPTION_DONE
        """The event type."""

    @dataclass
    class InputStartedEvent(EventBase):
        """Input started event."""

        item_id: str
        """The conversation item ID."""

        audio_start_ms: int
        """The audio start time in milliseconds."""

        type: Literal[
            ModelEventType.INPUT_STARTED
        ] = ModelEventType.INPUT_STARTED
        """The event type."""

    @dataclass
    class InputDoneEvent(EventBase):
        """Input done event."""

        item_id: str
        """The conversation item ID."""

        audio_end_ms: int
        """The audio end time in milliseconds."""

        type: Literal[ModelEventType.INPUT_DONE] = ModelEventType.INPUT_DONE
        """The event type."""

    @dataclass
    class ErrorEvent(EventBase):
        """Error event."""

        error_type: str
        """The error type."""

        code: str
        """The error code."""

        message: str
        """The error message."""

        type: Literal[ModelEventType.ERROR] = ModelEventType.ERROR
        """The event type."""

    class WebsocketConnectEvent(EventBase):
        """WebSocket connect event."""

        type: Literal[ModelEventType.WEBSOCKET_CONNECT]
        """The event type."""

    class WebsocketDisconnectEvent(EventBase):
        """WebSocket disconnect event."""

        type: Literal[ModelEventType.WEBSOCKET_DISCONNECT]
        """The event type."""
