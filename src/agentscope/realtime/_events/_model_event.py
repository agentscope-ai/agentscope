# -*- coding: utf-8 -*-
"""The unified event from realtime model APIs in AgentScope, which will be
consumed by the realtime agents."""
from enum import Enum
from typing import Literal

from pydantic import BaseModel

from ._utils import AudioFormat
from ...message import ToolUseBlock


class ModelEventType(str, Enum):
    """Types of model events from the API."""

    # API session lifecycle
    MODEL_SESSION_CREATED = "model_session_created"
    """The realtime API session has been created."""

    MODEL_SESSION_ENDED = "model_session_ended"
    """The realtime API session has ended."""

    # ============= MODEL GENERATED EVENTS =============

    MODEL_RESPONSE_CREATED = "model_response_created"
    """The realtime model begins generating a response."""

    MODEL_RESPONSE_DONE = "model_response_done"
    """The realtime model has finished generating a response."""

    # ============= MODEL RESPONSE CONTENT EVENTS =============

    MODEL_RESPONSE_AUDIO_DELTA = "model_response_audio_delta"
    """The realtime model response audio delta event."""

    MODEL_RESPONSE_AUDIO_DONE = "model_response_audio_done"
    """The realtime model response audio done event."""

    MODEL_RESPONSE_AUDIO_TRANSCRIPT_DELTA = (
        "model_response_audio_transcript_delta"
    )
    """The realtime model response audio transcript delta event."""

    MODEL_RESPONSE_AUDIO_TRANSCRIPT_DONE = (
        "model_response_audio_transcript_done"
    )
    """The realtime model response audio transcript done event."""

    MODEL_RESPONSE_TOOL_USE_DELTA = "model_response_tool_use_delta"
    """The realtime model response tool use delta event."""

    MODEL_RESPONSE_TOOL_USE_DONE = "model_response_tool_use_done"
    """The realtime model response tool use done event."""

    # Input transcription
    MODEL_INPUT_TRANSCRIPTION_DELTA = "model_input_transcription_delta"
    """The input transcription delta event."""

    MODEL_INPUT_TRANSCRIPTION_DONE = "model_input_transcription_done"
    """The input transcription done event."""

    # Input detection (VAD)
    MODEL_INPUT_STARTED = "model_input_started"
    """The input has started event."""

    MODEL_INPUT_DONE = "model_input_done"
    """The input has done event."""

    # ============= ERROR EVENTS =============

    MODEL_ERROR = "model_error"
    """An error event from the realtime model API."""

    # ============ WebSocket Events ============

    # WebSocket events (if used)
    MODEL_WEBSOCKET_CONNECT = "model_websocket_connect"
    """The model WebSocket has connected."""

    MODEL_WEBSOCKET_DISCONNECT = "model_websocket_disconnect"
    """The model WebSocket has disconnected."""


class ModelEvents:
    """The realtime model events that will be consumed by the realtime
    agents"""

    class EventBase(BaseModel):
        """The base class for all model events, used to unify the type
        hinting."""

    class ModelSessionCreatedEvent(EventBase):
        """Realtime model session created event.

        .. note:: This session is the connection between the realtime API and
              the client, not the conversation session.
        """

        session_id: str
        """The session ID."""

        type: Literal[
            ModelEventType.MODEL_SESSION_CREATED
        ] = ModelEventType.MODEL_SESSION_CREATED
        """The event type."""

    class ModelSessionEndedEvent(EventBase):
        """Session ended event.

        .. note:: This session is the connection between the realtime API and
              the client, not the conversation session.
        """

        session_id: str
        """The session ID."""

        reason: str
        """The reason for session end."""

        type: Literal[
            ModelEventType.MODEL_SESSION_ENDED
        ] = ModelEventType.MODEL_SESSION_ENDED
        """The event type."""

    class ModelResponseCreatedEvent(EventBase):
        """The realtime model begins generating a response."""

        response_id: str
        """The response ID."""

        type: Literal[
            ModelEventType.MODEL_RESPONSE_CREATED
        ] = ModelEventType.MODEL_RESPONSE_CREATED
        """The event type."""

    class ModelResponseDoneEvent(EventBase):
        """Model response done event."""

        response_id: str
        """The response ID."""

        input_tokens: int
        """The number of input tokens."""

        output_tokens: int
        """The number of output tokens."""

        metadata: dict[str, str] = {}
        """Additional metadata."""

        type: Literal[
            ModelEventType.MODEL_RESPONSE_DONE
        ] = ModelEventType.MODEL_RESPONSE_DONE
        """The event type."""

    class ModelResponseAudioDeltaEvent(EventBase):
        """Model response audio delta event."""

        response_id: str
        """The response ID."""

        item_id: str
        """The conversation item ID."""

        delta: str
        """The audio chunk data, encoded in base64."""

        format: AudioFormat
        """The audio format information."""

        type: Literal[
            ModelEventType.MODEL_RESPONSE_AUDIO_DELTA
        ] = ModelEventType.MODEL_RESPONSE_AUDIO_DELTA
        """The event type."""

    class ModelResponseAudioDoneEvent(EventBase):
        """Model response audio done event."""

        response_id: str
        """The response ID."""

        item_id: str
        """The conversation item ID."""

        type: Literal[
            ModelEventType.MODEL_RESPONSE_AUDIO_DONE
        ] = ModelEventType.MODEL_RESPONSE_AUDIO_DONE
        """The event type."""

    class ModelResponseAudioTranscriptDeltaEvent(EventBase):
        """Model response audio transcript delta event."""

        response_id: str
        """The response ID."""

        item_id: str
        """The conversation item ID."""

        delta: str
        """The transcript chunk data."""

        type: Literal[
            ModelEventType.MODEL_RESPONSE_AUDIO_TRANSCRIPT_DELTA
        ] = ModelEventType.MODEL_RESPONSE_AUDIO_TRANSCRIPT_DELTA
        """The event type."""

    class ModelResponseAudioTranscriptDoneEvent(EventBase):
        """Model response audio transcript done event."""

        response_id: str
        """The response ID."""

        item_id: str
        """The conversation item ID."""

        type: Literal[
            ModelEventType.MODEL_RESPONSE_AUDIO_TRANSCRIPT_DONE
        ] = ModelEventType.MODEL_RESPONSE_AUDIO_TRANSCRIPT_DONE
        """The event type."""

    class ModelResponseToolUseDeltaEvent(EventBase):
        """Model response tool use delta event."""

        response_id: str
        """The response ID."""

        item_id: str
        """The response item ID."""

        tool_use: ToolUseBlock
        """The tool use block delta, the arguments are accumulated in the
        `raw_input` field."""

        type: Literal[
            ModelEventType.MODEL_RESPONSE_TOOL_USE_DELTA
        ] = ModelEventType.MODEL_RESPONSE_TOOL_USE_DELTA
        """The event type."""

    class ModelResponseToolUseDoneEvent(EventBase):
        """Model response tool use done event."""

        response_id: str
        """The response ID."""

        item_id: str
        """The response item ID."""

        tool_use: ToolUseBlock
        """The complete tool use block."""

        type: Literal[
            ModelEventType.MODEL_RESPONSE_TOOL_USE_DONE
        ] = ModelEventType.MODEL_RESPONSE_TOOL_USE_DONE
        """The event type."""

    class ModelInputTranscriptionDeltaEvent(EventBase):
        """Input transcription delta event."""

        item_id: str
        """The conversation item ID."""

        delta: str
        """The transcription delta."""

        type: Literal[
            ModelEventType.MODEL_INPUT_TRANSCRIPTION_DELTA
        ] = ModelEventType.MODEL_INPUT_TRANSCRIPTION_DELTA
        """The event type."""

    class ModelInputTranscriptionDoneEvent(EventBase):
        """Input transcription done event."""

        transcript: str
        """The complete transcription."""

        item_id: str
        """The conversation item ID."""

        input_tokens: int | None = None
        """The number of input tokens."""

        output_tokens: int | None = None
        """The number of output tokens."""

        type: Literal[
            ModelEventType.MODEL_INPUT_TRANSCRIPTION_DONE
        ] = ModelEventType.MODEL_INPUT_TRANSCRIPTION_DONE
        """The event type."""

    class ModelInputStartedEvent(EventBase):
        """Input started event."""

        item_id: str
        """The conversation item ID."""

        audio_start_ms: int
        """The audio start time in milliseconds."""

        type: Literal[
            ModelEventType.MODEL_INPUT_STARTED
        ] = ModelEventType.MODEL_INPUT_STARTED
        """The event type."""

    class ModelInputDoneEvent(EventBase):
        """Input done event."""

        item_id: str
        """The conversation item ID."""

        audio_end_ms: int
        """The audio end time in milliseconds."""

        type: Literal[
            ModelEventType.MODEL_INPUT_DONE
        ] = ModelEventType.MODEL_INPUT_DONE
        """The event type."""

    class ModelErrorEvent(EventBase):
        """Error event."""

        error_type: str
        """The error type."""

        code: str
        """The error code."""

        message: str
        """The error message."""

        type: Literal[ModelEventType.MODEL_ERROR] = ModelEventType.MODEL_ERROR
        """The event type."""

    class WebsocketConnectEvent(EventBase):
        """WebSocket connect event."""

        type: Literal[ModelEventType.MODEL_WEBSOCKET_CONNECT]
        """The event type."""

    class WebsocketDisconnectEvent(EventBase):
        """WebSocket disconnect event."""

        type: Literal[ModelEventType.MODEL_WEBSOCKET_DISCONNECT]
        """The event type."""
