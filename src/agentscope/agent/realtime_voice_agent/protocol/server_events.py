# -*- coding: utf-8 -*-
"""Server Events - Events sent from backend to frontend.

All server events have type starting with "server.".
"""

from dataclasses import dataclass, field
from typing import Any, Literal


# =============================================================================
# Base Server Event
# =============================================================================


@dataclass
class ServerEvent:
    """Base class for all server events."""

    type: str
    event_id: str | None = None
    timestamp: int | None = None  # Unix timestamp in milliseconds
    session_id: str | None = None
    turn_id: str | None = None
    role: Literal["user", "assistant"] | None = None


# =============================================================================
# Session Events
# =============================================================================


@dataclass
class ServerSessionCreated(ServerEvent):
    """Session created successfully.

    Sent automatically after WebSocket connection is established.
    """

    agent_name: str = ""
    audio_config: dict[str, Any] | None = None
    type: str = field(default="server.session.created", init=False)


@dataclass
class ServerSessionUpdated(ServerEvent):
    """Session configuration updated.

    Sent in response to client.session.update.
    """

    config: dict[str, Any] | None = None
    type: str = field(default="server.session.updated", init=False)


@dataclass
class ServerSessionEnded(ServerEvent):
    """Session ended.

    Sent when session is closed (by client, timeout, or error).
    """

    reason: str = ""  # "client_request", "timeout", "error"
    type: str = field(default="server.session.ended", init=False)


# =============================================================================
# Turn Events
# =============================================================================


@dataclass
class ServerTurnStarted(ServerEvent):
    """New turn started.

    Sent when user input is detected.
    """

    type: str = field(default="server.turn.started", init=False)


@dataclass
class ServerTurnEnded(ServerEvent):
    """Turn ended.

    Sent when turn is complete or interrupted.
    """

    reason: str = ""  # "completed", "interrupted", "cancelled"
    type: str = field(default="server.turn.ended", init=False)


# =============================================================================
# Response Events
# =============================================================================


@dataclass
class ServerResponseStarted(ServerEvent):
    """Agent started generating response.

    Sent when model begins output.
    """

    response_id: str = ""
    type: str = field(default="server.response.started", init=False)


@dataclass
class ServerResponseDone(ServerEvent):
    """Response completed.

    Sent when model finishes output.
    """

    response_id: str = ""
    usage: dict[
        str,
        int,
    ] | None = None  # {"input_tokens": N, "output_tokens": M}
    type: str = field(default="server.response.done", init=False)


@dataclass
class ServerResponseCancelled(ServerEvent):
    """Response cancelled.

    Sent when response is interrupted by user or timeout.
    """

    response_id: str = ""
    reason: str = ""  # "user_interrupt", "timeout", "error"
    type: str = field(default="server.response.cancelled", init=False)


# =============================================================================
# Content Events
# =============================================================================


@dataclass
class ServerAudioDelta(ServerEvent):
    """Agent audio data chunk.

    Core event for streaming audio output.
    Audio is PCM 16-bit signed little-endian at 24kHz.
    """

    response_id: str = ""
    data: str = ""  # Base64 encoded PCM audio
    sample_rate: int = 24000  # Audio sample rate (Hz), fixed 24kHz
    seq: int | None = None  # Sequence number
    type: str = field(default="server.audio.delta", init=False)


@dataclass
class ServerAudioDone(ServerEvent):
    """Agent audio stream ended.

    Marks the end of audio output for current response.
    """

    response_id: str = ""
    type: str = field(default="server.audio.done", init=False)


@dataclass
class ServerTextDelta(ServerEvent):
    """Agent text chunk.

    Streaming text output for display.
    """

    response_id: str = ""
    text: str = ""
    type: str = field(default="server.text.delta", init=False)


@dataclass
class ServerTextDone(ServerEvent):
    """Agent text completed.

    Final text may differ from concatenated deltas.
    """

    response_id: str = ""
    text: str = ""  # Complete text
    type: str = field(default="server.text.done", init=False)


@dataclass
class ServerTranscriptUser(ServerEvent):
    """User speech transcription.

    Shows what the user said.
    """

    text: str = ""
    type: str = field(default="server.transcript.user", init=False)


@dataclass
class ServerTranscriptAgent(ServerEvent):
    """Agent speech transcription.

    Shows what the agent said (for accessibility/subtitles).
    """

    response_id: str = ""
    text: str = ""
    type: str = field(default="server.transcript.agent", init=False)


# =============================================================================
# VAD Events
# =============================================================================


@dataclass
class ServerSpeechStarted(ServerEvent):
    """User started speaking.

    For UI feedback: show recording indicator.
    """

    type: str = field(default="server.speech.started", init=False)


@dataclass
class ServerSpeechStopped(ServerEvent):
    """User stopped speaking.

    For UI feedback: show processing indicator.
    """

    type: str = field(default="server.speech.stopped", init=False)


# =============================================================================
# Tool Events
# =============================================================================


@dataclass
class ServerToolCall(ServerEvent):
    """Agent requests tool execution.

    For function calling: notify frontend to execute tool.
    """

    response_id: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    arguments: str | dict[str, Any] = ""  # JSON string or dict
    type: str = field(default="server.tool.call", init=False)


# =============================================================================
# System Events
# =============================================================================


@dataclass
class ServerPong(ServerEvent):
    """Heartbeat response.

    Sent in response to client.ping.
    """

    type: str = field(default="server.pong", init=False)


@dataclass
class ServerError(ServerEvent):
    """Error event.

    Notify frontend of errors.
    """

    code: str = ""
    message: str = ""
    retryable: bool = False
    type: str = field(default="server.error", init=False)


# =============================================================================
# Event Registry
# =============================================================================

SERVER_EVENT_TYPES = {
    "server.session.created": ServerSessionCreated,
    "server.session.updated": ServerSessionUpdated,
    "server.session.ended": ServerSessionEnded,
    "server.turn.started": ServerTurnStarted,
    "server.turn.ended": ServerTurnEnded,
    "server.response.started": ServerResponseStarted,
    "server.response.done": ServerResponseDone,
    "server.response.cancelled": ServerResponseCancelled,
    "server.audio.delta": ServerAudioDelta,
    "server.audio.done": ServerAudioDone,
    "server.text.delta": ServerTextDelta,
    "server.text.done": ServerTextDone,
    "server.transcript.user": ServerTranscriptUser,
    "server.transcript.agent": ServerTranscriptAgent,
    "server.speech.started": ServerSpeechStarted,
    "server.speech.stopped": ServerSpeechStopped,
    "server.tool.call": ServerToolCall,
    "server.pong": ServerPong,
    "server.error": ServerError,
}
