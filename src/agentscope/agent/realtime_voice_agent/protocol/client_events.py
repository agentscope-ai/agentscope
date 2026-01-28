# -*- coding: utf-8 -*-
"""Client Events - Events sent from frontend to backend.

All client events have type starting with "client.".
"""

from dataclasses import dataclass, field
from typing import Any, Literal


# =============================================================================
# Base Client Event
# =============================================================================


@dataclass
class ClientEvent:
    """Base class for all client events."""

    type: str
    event_id: str | None = None
    timestamp: int | None = None  # Unix timestamp in milliseconds


# =============================================================================
# Session Events
# =============================================================================


@dataclass
class SessionConfig:
    """Session configuration."""

    vad_mode: Literal["server", "none"] | None = None
    vad_threshold: float | None = None
    speech_timeout_ms: int | None = None
    input_audio_encoding: str | None = None
    input_audio_sample_rate: int | None = None
    voice: str | None = None
    instructions: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    input_transcription: bool | None = None
    output_transcription: bool | None = None
    tools: list[dict[str, Any]] | None = None


@dataclass
class ClientSessionUpdate(ClientEvent):
    """Update session configuration.

    Sent after WebSocket connection to configure the session.
    Can also be sent during the session to update configuration.
    """

    config: SessionConfig | dict[str, Any] = field(default_factory=dict)
    type: str = field(default="client.session.update", init=False)


@dataclass
class ClientSessionEnd(ClientEvent):
    """End the session explicitly.

    Sent when the user wants to close the session and release resources.
    """

    type: str = field(default="client.session.end", init=False)


# =============================================================================
# Audio Events
# =============================================================================


@dataclass
class ClientAudioAppend(ClientEvent):
    """Send audio data to the server.

    Audio data should be Base64 encoded PCM (16-bit signed little-endian).
    The server will resample if needed.
    """

    data: str = ""  # Base64 encoded PCM audio
    sample_rate: int = 16000  # Audio sample rate (Hz), default 16kHz
    seq: int | None = None  # Sequence number for ordering
    type: str = field(default="client.audio.append", init=False)


@dataclass
class ClientAudioCommit(ClientEvent):
    """Commit the audio buffer (manual mode).

    Sent when the user finishes speaking in manual/PTT mode.
    """

    type: str = field(default="client.audio.commit", init=False)


@dataclass
class ClientAudioClear(ClientEvent):
    """Clear the audio buffer.

    Sent to discard unprocessed audio (e.g., user cancels).
    """

    type: str = field(default="client.audio.clear", init=False)


# =============================================================================
# Text Events
# =============================================================================


@dataclass
class ClientTextSend(ClientEvent):
    """Send text message to the agent.

    Allows text-based interaction without voice input.
    The agent will respond with audio output.
    """

    text: str = ""  # The text message
    type: str = field(default="client.text.send", init=False)


# =============================================================================
# Image Events
# =============================================================================


@dataclass
class ClientImageAppend(ClientEvent):
    """Send image data to the server.

    Image data should be Base64 encoded.

    Requirements:
        - Format: JPEG (recommended), PNG, or WebP
        - Resolution: 480P or 720P recommended, max 1080P
        - Size: ≤500KB
        - Frequency: 1 image per second
        - Must send audio data before sending images
    """

    image: str = ""  # Base64 encoded image (without data URI prefix)
    mime_type: str = (
        "image/jpeg"  # MIME type: image/jpeg, image/png, image/webp
    )
    seq: int | None = None  # Sequence number
    type: str = field(default="client.image.append", init=False)


# =============================================================================
# Response Events
# =============================================================================


@dataclass
class ClientResponseCreate(ClientEvent):
    """Manually trigger agent response (manual mode).

    Sent when the user explicitly requests a response.
    """

    instructions: str | None = None  # Optional: override instructions
    modalities: list[str] | None = None  # Optional: ["audio", "text"]
    type: str = field(default="client.response.create", init=False)


@dataclass
class ClientResponseCancel(ClientEvent):
    """Cancel the current response.

    Sent when the user interrupts the agent's output.
    """

    type: str = field(default="client.response.cancel", init=False)


# =============================================================================
# Tool Events
# =============================================================================


@dataclass
class ClientToolResult(ClientEvent):
    """Return tool execution result.

    Sent after executing a tool requested by the agent.
    """

    tool_call_id: str = ""  # ID of the tool call
    result: str | dict[str, Any] = ""  # Tool execution result
    error: str | None = None  # Optional: error message if failed
    type: str = field(default="client.tool.result", init=False)


# =============================================================================
# System Events
# =============================================================================


@dataclass
class ClientPing(ClientEvent):
    """Heartbeat request.

    Sent to check connection status.
    """

    type: str = field(default="client.ping", init=False)


# =============================================================================
# Event Registry
# =============================================================================

CLIENT_EVENT_TYPES = {
    "client.session.update": ClientSessionUpdate,
    "client.session.end": ClientSessionEnd,
    "client.audio.append": ClientAudioAppend,
    "client.audio.commit": ClientAudioCommit,
    "client.audio.clear": ClientAudioClear,
    "client.text.send": ClientTextSend,
    "client.image.append": ClientImageAppend,
    "client.response.create": ClientResponseCreate,
    "client.response.cancel": ClientResponseCancel,
    "client.tool.result": ClientToolResult,
    "client.ping": ClientPing,
}
