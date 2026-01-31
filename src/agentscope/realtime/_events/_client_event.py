# -*- coding: utf-8 -*-
"""The client events for web-to-backend communication."""
from enum import Enum
from typing import List

from ...message import TextBlock, AudioBlock, ImageBlock, VideoBlock


class ClientEventType(str, Enum):
    """Types of client events for web-to-backend communication."""

    # ============== Session control ================
    CLIENT_SESSION_CREATE = "client.session.create"
    """The user creates a new session in the frontend."""

    CLIENT_SESSION_END = "client.session.end"
    """The user ends the current session in the frontend."""

    # ============== Response control ================
    CLIENT_RESPONSE_CREATE = "client.response.create"
    """The user requests the agent to generate a response immediately."""

    CLIENT_RESPONSE_CANCEL = "client.response.cancel"
    """The user interrupts the agent's current response generation."""

    CLIENT_IMAGE_APPEND = "client.image.append"
    """The user appends an image input to the current session."""

    CLIENT_TEXT_APPEND = "client.text.append"
    """The user appends a text input to the current session."""

    CLIENT_AUDIO_APPEND = "client.audio.append"
    """The user appends an audio input to the current session."""

    CLIENT_AUDIO_COMMIT = "client.audio.commit"
    """The user commits the audio input to signal end of input."""

    CLIENT_TOOL_RESULT = "client.tool.result"
    """The tool result executed in the frontend is sent back to the backend."""


class ClientEvents:
    """Realtime client events."""

    class ClientSessionCreateEvent:
        """Session create event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_SESSION_CREATE
        """The event type."""

        config: dict
        """The session config."""

    class ClientSessionEndEvent:
        """Session end event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_SESSION_END
        """The event type."""

        session_id: str
        """The session ID."""

    class ClientResponseCreateEvent:
        """Response create event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_RESPONSE_CREATE
        """The event type."""

        session_id: str
        """The session ID."""

    class ClientResponseCancelEvent:
        """Response cancel event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_RESPONSE_CANCEL
        """The event type."""

        session_id: str
        """The session ID."""

    class ClientImageAppendEvent:
        """Image append event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_IMAGE_APPEND
        """The event type."""

        session_id: str
        """The session ID."""

        data: str
        """The image data, encoded as base64 string."""

        format: dict
        """The image format information."""

    class ClientTextAppendEvent:
        """Text append event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_TEXT_APPEND
        """The event type."""

        session_id: str
        """The session ID."""

        text: str
        """The text data."""

    class ClientAudioAppendEvent:
        """Audio append event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_AUDIO_APPEND
        """The event type."""

        session_id: str
        """The session ID."""

        audio: str
        """The audio data, encoded as base64 string."""

        format: dict
        """The audio format information."""

    class ClientAudioCommitEvent:
        """Audio commit event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_AUDIO_COMMIT
        """The event type."""

        session_id: str
        """The session ID."""

    class ClientToolResultEvent:
        """Tool result event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_TOOL_RESULT
        """The event type."""

        session_id: str
        """The session ID."""

        id: str
        """The tool call ID."""

        name: str
        """The tool name."""

        output: str | List[TextBlock | ImageBlock | AudioBlock | VideoBlock]
        """The tool result."""
