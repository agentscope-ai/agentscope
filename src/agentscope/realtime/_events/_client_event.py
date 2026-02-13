# -*- coding: utf-8 -*-
"""The client events for web-to-backend communication."""
from enum import Enum
from typing import List

from pydantic import BaseModel

from ._utils import AudioFormat
from ...message import TextBlock, AudioBlock, ImageBlock, VideoBlock


class ClientEventType(str, Enum):
    """Types of client events for web-to-backend communication."""

    # ============== Session control ================
    CLIENT_SESSION_CREATE = "client_session_create"
    """The user creates a new session in the frontend."""

    CLIENT_SESSION_END = "client_session_end"
    """The user ends the current session in the frontend."""

    # ============== Response control ================
    CLIENT_RESPONSE_CREATE = "client_response_create"
    """The user requests the agent to generate a response immediately."""

    CLIENT_RESPONSE_CANCEL = "client_response_cancel"
    """The user interrupts the agent's current response generation."""

    CLIENT_IMAGE_APPEND = "client_image_append"
    """The user appends an image input to the current session."""

    CLIENT_TEXT_APPEND = "client_text_append"
    """The user appends a text input to the current session."""

    CLIENT_AUDIO_APPEND = "client_audio_append"
    """The user appends an audio input to the current session."""

    CLIENT_AUDIO_COMMIT = "client_audio_commit"
    """The user commits the audio input to signal end of input."""

    CLIENT_TOOL_RESULT = "client_tool_result"
    """The tool result executed in the frontend is sent back to the backend."""


class ClientEvents:
    """Realtime client events."""

    class EventBase(BaseModel):
        """The base class for all client events, used to unify the type
        hinting."""

    class ClientSessionCreateEvent(EventBase):
        """Session create event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_SESSION_CREATE
        """The event type."""

        config: dict
        """The session config."""

    class ClientSessionEndEvent(EventBase):
        """Session end event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_SESSION_END
        """The event type."""

        session_id: str
        """The session ID."""

    class ClientResponseCreateEvent(EventBase):
        """Response create event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_RESPONSE_CREATE
        """The event type."""

        session_id: str
        """The session ID."""

    class ClientResponseCancelEvent(EventBase):
        """Response cancel event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_RESPONSE_CANCEL
        """The event type."""

        session_id: str
        """The session ID."""

    class ClientImageAppendEvent(EventBase):
        """Image append event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_IMAGE_APPEND
        """The event type."""

        session_id: str
        """The session ID."""

        image: str
        """The image data, encoded as base64 string."""

        format: dict
        """The image format information."""

    class ClientTextAppendEvent(EventBase):
        """Text append event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_TEXT_APPEND
        """The event type."""

        session_id: str
        """The session ID."""

        text: str
        """The text data."""

    class ClientAudioAppendEvent(EventBase):
        """Audio append event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_AUDIO_APPEND
        """The event type."""

        session_id: str
        """The session ID."""

        audio: str
        """The audio data, encoded as base64 string."""

        format: AudioFormat
        """The audio format information."""

    class ClientAudioCommitEvent(EventBase):
        """Audio commit event in the frontend"""

        type: ClientEventType = ClientEventType.CLIENT_AUDIO_COMMIT
        """The event type."""

        session_id: str
        """The session ID."""

    class ClientToolResultEvent(EventBase):
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

    MAPPING = {
        ClientEventType.CLIENT_SESSION_CREATE: ClientSessionCreateEvent,
        ClientEventType.CLIENT_SESSION_END: ClientSessionEndEvent,
        ClientEventType.CLIENT_RESPONSE_CREATE: ClientResponseCreateEvent,
        ClientEventType.CLIENT_RESPONSE_CANCEL: ClientResponseCancelEvent,
        ClientEventType.CLIENT_IMAGE_APPEND: ClientImageAppendEvent,
        ClientEventType.CLIENT_TEXT_APPEND: ClientTextAppendEvent,
        ClientEventType.CLIENT_AUDIO_APPEND: ClientAudioAppendEvent,
        ClientEventType.CLIENT_AUDIO_COMMIT: ClientAudioCommitEvent,
        ClientEventType.CLIENT_TOOL_RESULT: ClientToolResultEvent,
    }

    @classmethod
    def from_json(cls, json_data: dict) -> EventBase:
        """Parse the client event from JSON data and return the corresponding
        event instance.

        Args:
            json_data (`dict`):
                The JSON data, which must contain the "type" field.

        Raises:
            `ValueError`:
                If the event type is unknown.

        Returns:
            `ClientEvents.EventBase`:
                The corresponding client event instance.
        """
        if not isinstance(json_data, dict) or "type" not in json_data:
            raise ValueError(
                f"Invalid JSON data for ClientEvent: {json_data}",
            )

        event_type = json_data["type"]

        if event_type not in cls.MAPPING:
            raise ValueError(f"Unknown ClientEvent type: {event_type}")

        # Obtain the event class from the mapping
        event_class = cls.MAPPING[event_type]
        return event_class(**json_data)
