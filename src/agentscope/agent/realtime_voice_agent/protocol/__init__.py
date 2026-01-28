# -*- coding: utf-8 -*-
"""Protocol definitions for Realtime Voice Agent WebSocket communication.

This module defines the WebSocket protocol between frontend and backend:
- Client Events: Events sent from frontend to backend
- Server Events: Events sent from backend to frontend
- Serialization: JSON serialization/deserialization utilities
"""

from .client_events import (
    # Base
    ClientEvent,
    SessionConfig,
    # Session
    ClientSessionUpdate,
    ClientSessionEnd,
    # Audio
    ClientAudioAppend,
    ClientAudioCommit,
    ClientAudioClear,
    # Text
    ClientTextSend,
    # Image
    ClientImageAppend,
    # Response
    ClientResponseCreate,
    ClientResponseCancel,
    # Tool
    ClientToolResult,
    # System
    ClientPing,
)

from .server_events import (
    # Base
    ServerEvent,
    # Session
    ServerSessionCreated,
    ServerSessionUpdated,
    ServerSessionEnded,
    # Turn
    ServerTurnStarted,
    ServerTurnEnded,
    # Response
    ServerResponseStarted,
    ServerResponseDone,
    ServerResponseCancelled,
    # Content
    ServerAudioDelta,
    ServerAudioDone,
    ServerTextDelta,
    ServerTextDone,
    ServerTranscriptUser,
    ServerTranscriptAgent,
    # VAD
    ServerSpeechStarted,
    ServerSpeechStopped,
    # Tool
    ServerToolCall,
    # System
    ServerPong,
    ServerError,
)

from .serializer import (
    serialize_event,
    deserialize_client_event,
    deserialize_server_event,
    event_to_dict,
)

__all__ = [
    # Client Events
    "ClientEvent",
    "SessionConfig",
    "ClientSessionUpdate",
    "ClientSessionEnd",
    "ClientAudioAppend",
    "ClientAudioCommit",
    "ClientAudioClear",
    "ClientTextSend",
    "ClientImageAppend",
    "ClientResponseCreate",
    "ClientResponseCancel",
    "ClientToolResult",
    "ClientPing",
    # Server Events
    "ServerEvent",
    "ServerSessionCreated",
    "ServerSessionUpdated",
    "ServerSessionEnded",
    "ServerTurnStarted",
    "ServerTurnEnded",
    "ServerResponseStarted",
    "ServerResponseDone",
    "ServerResponseCancelled",
    "ServerAudioDelta",
    "ServerAudioDone",
    "ServerTextDelta",
    "ServerTextDone",
    "ServerTranscriptUser",
    "ServerTranscriptAgent",
    "ServerSpeechStarted",
    "ServerSpeechStopped",
    "ServerToolCall",
    "ServerPong",
    "ServerError",
    # Serialization
    "serialize_event",
    "deserialize_client_event",
    "deserialize_server_event",
    "event_to_dict",
]
