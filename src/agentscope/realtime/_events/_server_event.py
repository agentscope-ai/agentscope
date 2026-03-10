# -*- coding: utf-8 -*-
"""The websocket events generated from the realtime agent and backend."""
from enum import Enum
from typing import Literal

from pydantic import BaseModel

from ._utils import AudioFormat
from ._model_event import ModelEvents
from ...message import ToolUseBlock, ToolResultBlock


class ServerEventType(str, Enum):
    """Types of agent events for backend-to-web communication."""

    # Session lifecycle
    SERVER_SESSION_CREATED = "server_session_created"
    """The session between the web frontend and backend is created."""

    SERVER_SESSION_UPDATED = "server_session_updated"
    """The session between the web frontend and backend is updated."""

    SERVER_SESSION_ENDED = "server_session_ended"
    """The session between the web frontend and backend is ended."""

    # ============== AGENT LIFECYCLE EVENTS ================

    AGENT_READY = "agent_ready"
    """The agent is created and ready to receive inputs."""

    AGENT_ENDED = "agent_ended"
    """The agent ended."""

    # ============== AGENT RESPONSE EVENTS =================

    # Response events
    AGENT_RESPONSE_CREATED = "agent_response_created"
    """The agent starts generating a response."""

    AGENT_RESPONSE_DONE = "agent_response_done"
    """The agent finished generating a response."""

    # ============== Response content events =================

    AGENT_RESPONSE_AUDIO_DELTA = "agent_response_audio_delta"
    """The agent's response audio data delta."""

    AGENT_RESPONSE_AUDIO_DONE = "agent_response_audio_done"
    """The agent's response audio data is complete."""

    AGENT_RESPONSE_AUDIO_TRANSCRIPT_DELTA = (
        "agent_response_audio_transcript_delta"
    )
    """The agent's response audio transcription delta."""

    AGENT_RESPONSE_AUDIO_TRANSCRIPT_DONE = (
        "agent_response_audio_transcript_done"
    )
    """The agent's response audio transcription is complete."""

    AGENT_RESPONSE_TOOL_USE_DELTA = "agent_response_tool_use_delta"
    """The agent's response tool use data delta."""

    AGENT_RESPONSE_TOOL_USE_DONE = "agent_response_tool_use_done"
    """The agent's response tool use data is complete."""

    AGENT_RESPONSE_TOOL_RESULT = "agent_response_tool_result"
    """The tool execution result."""

    # ============== INPUT AUDIO TRANSCRIPTION EVENTS =================

    AGENT_INPUT_TRANSCRIPTION_DELTA = "agent_input_transcription_delta"
    """The input audio transcription delta."""

    AGENT_INPUT_TRANSCRIPTION_DONE = "agent_input_transcription_done"
    """The input audio transcription is complete."""

    # Input detection
    AGENT_INPUT_STARTED = "agent_input_started"
    """Detected the start of user input audio."""

    AGENT_INPUT_DONE = "agent_input_done"
    """Detected the end of user input audio."""

    # ============== ERROR EVENTS =================

    AGENT_ERROR = "agent_error"
    """An error occurred in the backend or agent."""


class ServerEvents:
    """Realtime server events."""

    class EventBase(BaseModel):
        """The base class for all server events, used to unify the type
        hinting."""

    class ServerSessionCreatedEvent(EventBase):
        """Server session created event in the backend"""

        session_id: str
        """The session ID."""

        type: Literal[
            ServerEventType.SERVER_SESSION_CREATED
        ] = ServerEventType.SERVER_SESSION_CREATED
        """The event type."""

    class ServerSessionUpdatedEvent(EventBase):
        """Server session updated event in the backend"""

        session_id: str
        """The session ID."""

        type: Literal[
            ServerEventType.SERVER_SESSION_UPDATED
        ] = ServerEventType.SERVER_SESSION_UPDATED
        """The event type."""

    class ServerSessionEndedEvent(EventBase):
        """Server Session ended event in the backend"""

        session_id: str
        """The session ID."""

        type: Literal[
            ServerEventType.SERVER_SESSION_ENDED
        ] = ServerEventType.SERVER_SESSION_ENDED
        """The event type."""

    class AgentReadyEvent(EventBase):
        """Agent ready event in the backend"""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_READY
        ] = ServerEventType.AGENT_READY
        """The event type."""

    class AgentEndedEvent(EventBase):
        """Agent ended event in the backend"""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_ENDED
        ] = ServerEventType.AGENT_ENDED
        """The event type."""

    class AgentResponseCreatedEvent(EventBase):
        """Response created event in the backend"""

        response_id: str
        """The response ID."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_RESPONSE_CREATED
        ] = ServerEventType.AGENT_RESPONSE_CREATED
        """The event type."""

    class AgentResponseDoneEvent(EventBase):
        """Response done event in the backend"""

        response_id: str
        """The response ID."""

        input_tokens: int
        """The number of input tokens used."""

        output_tokens: int
        """The number of output tokens used."""

        metadata: dict[str, str] = {}
        """Additional metadata about the response."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_RESPONSE_DONE
        ] = ServerEventType.AGENT_RESPONSE_DONE
        """The event type."""

    class AgentResponseAudioDeltaEvent(EventBase):
        """Response audio delta event in the backend"""

        response_id: str
        """The response ID."""

        item_id: str
        """The response item ID."""

        delta: str
        """The audio chunk data, encoded as base64 string."""

        format: AudioFormat
        """The audio format information."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_RESPONSE_AUDIO_DELTA
        ] = ServerEventType.AGENT_RESPONSE_AUDIO_DELTA
        """The event type."""

    class AgentResponseAudioDoneEvent(EventBase):
        """Response audio done event in the backend"""

        response_id: str
        """The response ID."""

        item_id: str
        """The response item ID."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_RESPONSE_AUDIO_DONE
        ] = ServerEventType.AGENT_RESPONSE_AUDIO_DONE

    class AgentResponseAudioTranscriptDeltaEvent(EventBase):
        """Response audio transcript delta event in the backend"""

        response_id: str
        """The response ID."""

        item_id: str
        """The response item ID."""

        delta: str
        """The transcript chunk data."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_RESPONSE_AUDIO_TRANSCRIPT_DELTA
        ] = ServerEventType.AGENT_RESPONSE_AUDIO_TRANSCRIPT_DELTA
        """The event type."""

    class AgentResponseAudioTranscriptDoneEvent(EventBase):
        """Response audio transcript done event in the backend"""

        response_id: str
        """The response ID."""

        item_id: str
        """The response item ID."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_RESPONSE_AUDIO_TRANSCRIPT_DONE
        ] = ServerEventType.AGENT_RESPONSE_AUDIO_TRANSCRIPT_DONE
        """The event type."""

    class AgentResponseToolUseDeltaEvent(EventBase):
        """Response tool use delta event in the backend"""

        response_id: str
        """The response ID."""

        item_id: str
        """The response item ID."""

        tool_use: ToolUseBlock
        """The tool use block delta, the arguments are accumulated in the
        `raw_input` field."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_RESPONSE_TOOL_USE_DELTA
        ] = ServerEventType.AGENT_RESPONSE_TOOL_USE_DELTA
        """The event type."""

    class AgentResponseToolUseDoneEvent(EventBase):
        """Response tool use done event in the backend"""

        response_id: str
        """The response ID."""

        item_id: str
        """The response item ID."""

        tool_use: ToolUseBlock
        """The complete tool use block."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_RESPONSE_TOOL_USE_DONE
        ] = ServerEventType.AGENT_RESPONSE_TOOL_USE_DONE
        """The event type."""

    class AgentResponseToolResultEvent(EventBase):
        """Response tool result event"""

        tool_result: ToolResultBlock
        """The tool result block."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_RESPONSE_TOOL_RESULT
        ] = ServerEventType.AGENT_RESPONSE_TOOL_RESULT
        """The event type."""

    class AgentInputTranscriptionDeltaEvent(EventBase):
        """Input transcription delta event in the backend"""

        item_id: str
        """The conversation item ID."""

        delta: str
        """The transcription chunk data."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_INPUT_TRANSCRIPTION_DELTA
        ] = ServerEventType.AGENT_INPUT_TRANSCRIPTION_DELTA
        """The event type."""

    class AgentInputTranscriptionDoneEvent(EventBase):
        """Input transcription done event in the backend"""

        transcript: str
        """The complete transcription text."""

        item_id: str
        """The conversation item ID."""

        input_tokens: int | None = None
        """The number of input tokens."""

        output_tokens: int | None = None
        """The number of output tokens."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_INPUT_TRANSCRIPTION_DONE
        ] = ServerEventType.AGENT_INPUT_TRANSCRIPTION_DONE
        """The event type."""

    class AgentInputStartedEvent(EventBase):
        """Input started event in the backend"""

        item_id: str
        """The conversation item ID."""

        audio_start_ms: int
        """The audio start time in milliseconds."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_INPUT_STARTED
        ] = ServerEventType.AGENT_INPUT_STARTED
        """The event type."""

    class AgentInputDoneEvent(EventBase):
        """Input done event in the backend"""

        item_id: str
        """The conversation item ID."""

        audio_end_ms: int
        """The audio end time in milliseconds."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_INPUT_DONE
        ] = ServerEventType.AGENT_INPUT_DONE
        """The event type."""

    class AgentErrorEvent(EventBase):
        """Error event in the backend"""

        error_type: str
        """The error type."""

        code: str
        """The error code."""

        message: str
        """The error message."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_ERROR
        ] = ServerEventType.AGENT_ERROR
        """The event type."""

    @classmethod
    def from_model_event(
        cls,
        model_event: ModelEvents.ModelResponseCreatedEvent
        | ModelEvents.ModelResponseDoneEvent
        | ModelEvents.ModelResponseAudioDeltaEvent
        | ModelEvents.ModelResponseAudioDoneEvent
        | ModelEvents.ModelResponseAudioTranscriptDeltaEvent
        | ModelEvents.ModelResponseAudioTranscriptDoneEvent
        | ModelEvents.ModelResponseToolUseDeltaEvent
        | ModelEvents.ModelResponseToolUseDoneEvent
        | ModelEvents.ModelInputTranscriptionDeltaEvent
        | ModelEvents.ModelInputTranscriptionDoneEvent
        | ModelEvents.ModelInputStartedEvent
        | ModelEvents.ModelInputDoneEvent
        | ModelEvents.ModelErrorEvent,
        agent_id: str,
        agent_name: str,
    ) -> EventBase:
        """Convert a model event to a server event quickly with
        1) replace the "model_" prefix with "agent_" in the type field; 2) add
        agent_id and agent_name fields

        Args:
            model_event (`ModelEvents.EventBase`):
                The model event to convert.
            agent_id (`str`):
                The agent ID.
            agent_name (`str`):
                The agent name.

        Returns:
            `ServerEvents.EventBase`:
                The converted server event.
        """
        # Obtain the corresponding agent event class
        cls_name = model_event.__class__.__name__.replace("Model", "Agent")
        agent_event_cls = getattr(cls, cls_name)

        # The data dict of the model event
        model_event_dict = model_event.model_dump()

        # 1) Replace the "model_" prefix with "agent_" in the type field
        if "type" in model_event_dict:
            model_event_dict["type"] = model_event_dict["type"].replace(
                "model_",
                "agent_",
            )

        try:
            # 2) Add agent_id and agent_name fields
            model_event_dict["agent_id"] = agent_id
            model_event_dict["agent_name"] = agent_name
            agent_event = agent_event_cls.model_validate(model_event_dict)

        except Exception as e:
            raise RuntimeError(
                f"Failed to convert model event {model_event} to agent "
                f"event {agent_event_cls}: {e}",
            ) from e

        return agent_event
