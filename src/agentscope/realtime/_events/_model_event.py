# -*- coding: utf-8 -*-
"""The unified event types emitted by realtime model APIs in AgentScope.

These are *internal* to the realtime module: realtime model implementations
parse vendor-specific WebSocket messages into ``ModelEvents.*`` instances.
Downstream consumers (e.g. a realtime agent) translate them into the
public ``AgentEvent`` stream.
"""
from enum import Enum
from typing import Literal

from pydantic import BaseModel

from ._utils import AudioFormat
from ...message import ToolCallBlock


class ModelEventType(str, Enum):
    """Types of events emitted by a realtime model."""

    # ============ Session lifecycle ============
    MODEL_SESSION_CREATED = "model_session_created"
    MODEL_SESSION_ENDED = "model_session_ended"

    # ============ Response lifecycle ============
    MODEL_RESPONSE_CREATED = "model_response_created"
    MODEL_RESPONSE_DONE = "model_response_done"

    # ============ Response content ============
    MODEL_RESPONSE_AUDIO_DELTA = "model_response_audio_delta"
    MODEL_RESPONSE_AUDIO_DONE = "model_response_audio_done"

    MODEL_RESPONSE_AUDIO_TRANSCRIPT_DELTA = (
        "model_response_audio_transcript_delta"
    )
    MODEL_RESPONSE_AUDIO_TRANSCRIPT_DONE = (
        "model_response_audio_transcript_done"
    )

    MODEL_RESPONSE_TOOL_CALL_DELTA = "model_response_tool_call_delta"
    MODEL_RESPONSE_TOOL_CALL_DONE = "model_response_tool_call_done"

    # ============ Input transcription / VAD ============
    MODEL_INPUT_TRANSCRIPTION_DELTA = "model_input_transcription_delta"
    MODEL_INPUT_TRANSCRIPTION_DONE = "model_input_transcription_done"

    MODEL_INPUT_STARTED = "model_input_started"
    MODEL_INPUT_DONE = "model_input_done"

    # ============ Session resumption (Gemini) ============
    MODEL_SESSION_RESUMPTION = "model_session_resumption"

    # ============ Error ============
    MODEL_ERROR = "model_error"


class ModelEvents:
    """Namespace of Pydantic event types emitted by a realtime model.

    Implementations call ``parse_api_message`` and return one or more of
    these.  They are consumed inside the realtime module by whatever is
    pumping the websocket; they are not meant to be persisted or shipped
    over the public AgentEvent stream as-is.
    """

    class EventBase(BaseModel):
        """Common base class for type-hinting only."""

    # ---------------- Session ----------------

    class ModelSessionCreatedEvent(EventBase):
        """The realtime API session has been created.

        .. note:: This session is the WebSocket connection between the
              realtime API and the client, not the conversation session.
        """

        session_id: str
        type: Literal[
            ModelEventType.MODEL_SESSION_CREATED
        ] = ModelEventType.MODEL_SESSION_CREATED

    class ModelSessionEndedEvent(EventBase):
        """The realtime API session has ended."""

        session_id: str
        reason: str = ""
        type: Literal[
            ModelEventType.MODEL_SESSION_ENDED
        ] = ModelEventType.MODEL_SESSION_ENDED

    # ---------------- Response lifecycle ----------------

    class ModelResponseCreatedEvent(EventBase):
        """The realtime model begins generating a response."""

        response_id: str
        type: Literal[
            ModelEventType.MODEL_RESPONSE_CREATED
        ] = ModelEventType.MODEL_RESPONSE_CREATED

    class ModelResponseDoneEvent(EventBase):
        """The realtime model has finished generating a response."""

        response_id: str
        input_tokens: int = 0
        output_tokens: int = 0
        metadata: dict[str, str] = {}
        type: Literal[
            ModelEventType.MODEL_RESPONSE_DONE
        ] = ModelEventType.MODEL_RESPONSE_DONE

    # ---------------- Response audio ----------------

    class ModelResponseAudioDeltaEvent(EventBase):
        """Incremental audio chunk from the model (base64-encoded)."""

        response_id: str
        item_id: str
        delta: str
        format: AudioFormat
        type: Literal[
            ModelEventType.MODEL_RESPONSE_AUDIO_DELTA
        ] = ModelEventType.MODEL_RESPONSE_AUDIO_DELTA

    class ModelResponseAudioDoneEvent(EventBase):
        """The model has finished emitting audio for this item."""

        response_id: str
        item_id: str
        type: Literal[
            ModelEventType.MODEL_RESPONSE_AUDIO_DONE
        ] = ModelEventType.MODEL_RESPONSE_AUDIO_DONE

    # ---------------- Response transcript ----------------

    class ModelResponseAudioTranscriptDeltaEvent(EventBase):
        """Incremental transcript of the model's spoken audio."""

        response_id: str
        item_id: str
        delta: str
        type: Literal[
            ModelEventType.MODEL_RESPONSE_AUDIO_TRANSCRIPT_DELTA
        ] = ModelEventType.MODEL_RESPONSE_AUDIO_TRANSCRIPT_DELTA

    class ModelResponseAudioTranscriptDoneEvent(EventBase):
        """The transcript of the model's spoken audio is complete."""

        response_id: str
        item_id: str
        type: Literal[
            ModelEventType.MODEL_RESPONSE_AUDIO_TRANSCRIPT_DONE
        ] = ModelEventType.MODEL_RESPONSE_AUDIO_TRANSCRIPT_DONE

    # ---------------- Tool calls ----------------

    class ModelResponseToolCallDeltaEvent(EventBase):
        """Incremental tool-call delta.  Arguments accumulate in
        ``tool_call.input`` across deltas."""

        response_id: str
        item_id: str
        tool_call: ToolCallBlock
        type: Literal[
            ModelEventType.MODEL_RESPONSE_TOOL_CALL_DELTA
        ] = ModelEventType.MODEL_RESPONSE_TOOL_CALL_DELTA

    class ModelResponseToolCallDoneEvent(EventBase):
        """The complete tool-call block is ready."""

        response_id: str
        item_id: str
        tool_call: ToolCallBlock
        type: Literal[
            ModelEventType.MODEL_RESPONSE_TOOL_CALL_DONE
        ] = ModelEventType.MODEL_RESPONSE_TOOL_CALL_DONE

    # ---------------- Input transcription ----------------

    class ModelInputTranscriptionDeltaEvent(EventBase):
        """Incremental transcription of the user's input audio."""

        item_id: str
        delta: str
        type: Literal[
            ModelEventType.MODEL_INPUT_TRANSCRIPTION_DELTA
        ] = ModelEventType.MODEL_INPUT_TRANSCRIPTION_DELTA

    class ModelInputTranscriptionDoneEvent(EventBase):
        """The complete transcription of the user's input audio."""

        item_id: str
        transcript: str
        input_tokens: int | None = None
        output_tokens: int | None = None
        type: Literal[
            ModelEventType.MODEL_INPUT_TRANSCRIPTION_DONE
        ] = ModelEventType.MODEL_INPUT_TRANSCRIPTION_DONE

    # ---------------- VAD ----------------

    class ModelInputStartedEvent(EventBase):
        """Server VAD detected speech start."""

        item_id: str
        audio_start_ms: int = 0
        type: Literal[
            ModelEventType.MODEL_INPUT_STARTED
        ] = ModelEventType.MODEL_INPUT_STARTED

    class ModelInputDoneEvent(EventBase):
        """Server VAD detected speech end."""

        item_id: str
        audio_end_ms: int = 0
        type: Literal[
            ModelEventType.MODEL_INPUT_DONE
        ] = ModelEventType.MODEL_INPUT_DONE

    # ---------------- Session resumption ----------------

    class ModelSessionResumptionEvent(EventBase):
        """A session resumption handle update from the API.

        Currently only the Gemini Live API supports this: the server
        periodically sends a ``sessionResumptionUpdate`` carrying a
        ``new_handle`` that the client should store and pass back on
        the next ``connect()`` to restore context.
        """

        handle: str
        type: Literal[
            ModelEventType.MODEL_SESSION_RESUMPTION
        ] = ModelEventType.MODEL_SESSION_RESUMPTION

    # ---------------- Error ----------------

    class ModelErrorEvent(EventBase):
        """An error reported by the realtime API."""

        error_type: str
        code: str
        message: str
        type: Literal[ModelEventType.MODEL_ERROR] = ModelEventType.MODEL_ERROR
