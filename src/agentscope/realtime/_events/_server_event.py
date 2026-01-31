# -*- coding: utf-8 -*-
"""The websocket events generated from the realtime agent and backend."""
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from agentscope.message import TextBlock, ImageBlock, AudioBlock, VideoBlock


class ServerEventType(str, Enum):
    """Types of agent events for backend-to-web communication."""

    # Session lifecycle
    SESSION_CREATED = "session_created"
    SESSION_UPDATED = "session_updated"
    SESSION_ENDED = "session_ended"

    # ============== AGENT LIFECYCLE EVENTS ================

    AGENT_READY = "agent_ready"
    """The agent is created and ready to receive inputs."""

    AGENT_ENDED = "agent_ended"
    """The agent ended."""

    # ============== AGENT RESPONSE EVENTS =================

    # Response events
    RESPONSE_CREATED = "response_created"
    """The agent starts generating a response."""

    RESPONSE_CANCELLED = "response_cancelled"
    """The agent's response generation is interrupted/cancelled."""

    RESPONSE_DONE = "response_done"
    """The agent finished generating a response."""

    # ============== Response content events =================

    RESPONSE_AUDIO_DELTA = "response_audio_delta"
    """The agent's response audio data delta."""

    RESPONSE_AUDIO_DONE = "response_audio_done"
    """The agent's response audio data is complete."""

    RESPONSE_AUDIO_TRANSCRIPT_DELTA = "response_audio_transcript_delta"
    """The agent's response audio transcription delta."""

    RESPONSE_AUDIO_TRANSCRIPT_DONE = "response_audio_transcript_done"
    """The agent's response audio transcription is complete."""

    RESPONSE_TOOL_USE_DELTA = "response_tool_use_delta"
    """The agent's response tool use data delta."""

    RESPONSE_TOOL_USE_DONE = "response_tool_use_done"
    """The agent's response tool use data is complete."""

    RESPONSE_TOOL_RESULT = "response_tool_result"
    """The tool execution result."""

    # ============== INPUT AUDIO TRANSCRIPTION EVENTS =================

    INPUT_TRANSCRIPTION_DELTA = "input_transcription_delta"
    """The input audio transcription delta."""

    INPUT_TRANSCRIPTION_DONE = "input_transcription_done"
    """The input audio transcription is complete."""

    # Input detection
    INPUT_STARTED = "input_started"
    """Detected the start of user input audio."""

    INPUT_DONE = "input_done"
    """Detected the end of user input audio."""

    # ============== ERROR EVENTS =================

    ERROR = "error"


class ServerEvents:
    """Realtime server events."""

    @dataclass
    class SessionCreatedEvent:
        """Session created event in the backend"""

        session_id: str
        """The session ID."""

        type: Literal[
            ServerEventType.SESSION_CREATED
        ] = ServerEventType.SESSION_CREATED
        """The event type."""

    @dataclass
    class SessionUpdatedEvent:
        """Session updated event in the backend"""

        session_id: str
        """The session ID."""

        type: Literal[
            ServerEventType.SESSION_UPDATED
        ] = ServerEventType.SESSION_UPDATED
        """The event type."""

    @dataclass
    class SessionEndedEvent:
        """Session ended event in the backend"""

        session_id: str
        """The session ID."""

        type: Literal[
            ServerEventType.SESSION_ENDED
        ] = ServerEventType.SESSION_ENDED
        """The event type."""

    @dataclass
    class AgentReadyEvent:
        """Agent ready event in the backend"""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_READY
        ] = ServerEventType.AGENT_READY
        """The event type."""

    @dataclass
    class AgentEndedEvent:
        """Agent ended event in the backend"""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.AGENT_ENDED
        ] = ServerEventType.AGENT_ENDED
        """The event type."""

    @dataclass
    class AgentResponseCreatedEvent:
        """Response created event in the backend"""

        response_id: str
        """The response ID."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.RESPONSE_CREATED
        ] = ServerEventType.RESPONSE_CREATED
        """The event type."""

    @dataclass
    class AgentResponseDoneEvent:
        """Response done event in the backend"""

        response_id: str
        """The response ID."""

        input_tokens: int
        """The number of input tokens used."""

        output_tokens: int
        """The number of output tokens used."""

        metadata: dict[str, str]
        """Additional metadata about the response."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.RESPONSE_DONE
        ] = ServerEventType.RESPONSE_DONE
        """The event type."""

    @dataclass
    class AgentResponseAudioDeltaEvent:
        """Response audio delta event in the backend"""

        response_id: str
        """The response ID."""

        item_id: str
        """The response item ID."""

        delta: str
        """The audio chunk data, encoded as base64 string."""

        format: dict
        """The audio format information."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.RESPONSE_AUDIO_DELTA
        ] = ServerEventType.RESPONSE_AUDIO_DELTA
        """The event type."""

    @dataclass
    class AgentResponseAudioDoneEvent:
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
            ServerEventType.RESPONSE_AUDIO_DONE
        ] = ServerEventType.RESPONSE_AUDIO_DONE

    @dataclass
    class AgentResponseAudioTranscriptDeltaEvent:
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
            ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA
        ] = ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA
        """The event type."""

    @dataclass
    class AgentResponseAudioTranscriptDoneEvent:
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
            ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE
        ] = ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE
        """The event type."""

    @dataclass
    class AgentResponseToolUseDeltaEvent:
        """Response tool use delta event in the backend"""

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

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.RESPONSE_TOOL_USE_DELTA
        ] = ServerEventType.RESPONSE_TOOL_USE_DELTA
        """The event type."""

    @dataclass
    class AgentResponseToolUseDoneEvent:
        """Response tool use done event in the backend"""

        response_id: str
        """The response ID."""

        item_id: str
        """The response item ID."""

        call_id: str
        """The tool call ID."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.RESPONSE_TOOL_USE_DONE
        ] = ServerEventType.RESPONSE_TOOL_USE_DONE
        """The event type."""

    @dataclass
    class AgentResponseToolResultEvent:
        """Response tool result event"""

        call_id: str
        """The tool call ID."""

        name: str
        """The tool name."""

        output: str | list[TextBlock | ImageBlock | AudioBlock | VideoBlock]
        """The tool output."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.RESPONSE_TOOL_RESULT
        ] = ServerEventType.RESPONSE_TOOL_RESULT
        """The event type."""

    @dataclass
    class AgentInputTranscriptionDeltaEvent:
        """Input transcription delta event in the backend"""

        delta: str
        """The transcription chunk data."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.INPUT_TRANSCRIPTION_DELTA
        ] = ServerEventType.INPUT_TRANSCRIPTION_DELTA
        """The event type."""

    @dataclass
    class AgentInputTranscriptionDoneEvent:
        """Input transcription done event in the backend"""

        transcript: str
        """The complete transcription text."""

        input_tokens: int
        """The number of input tokens."""

        output_tokens: int
        """The number of output tokens."""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.INPUT_TRANSCRIPTION_DONE
        ] = ServerEventType.INPUT_TRANSCRIPTION_DONE
        """The event type."""

    @dataclass
    class AgentInputStartedEvent:
        """Input started event in the backend"""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[
            ServerEventType.INPUT_STARTED
        ] = ServerEventType.INPUT_STARTED
        """The event type."""

    @dataclass
    class AgentInputDoneEvent:
        """Input done event in the backend"""

        agent_id: str
        """The agent ID."""

        agent_name: str
        """The agent name."""

        type: Literal[ServerEventType.INPUT_DONE] = ServerEventType.INPUT_DONE
        """The event type."""

    @dataclass
    class AgentErrorEvent:
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

        type: Literal[ServerEventType.ERROR] = ServerEventType.ERROR
        """The event type."""
