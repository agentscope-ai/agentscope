# -*- coding: utf-8 -*-
"""Live voice agent module with callback-based architecture.

This module provides:
- RealtimeVoiceAgent: Agent with incoming_queue and callback pattern
- RealtimeVoiceModelBase: Base class for callback-based models
- DashScopeRealtimeModel: DashScope implementation
- EventMsgStream: Central queue with dispatch loop
- ModelEvent/AgentEvent: Unified event system
"""

from .events import (
    # Model Event Types
    ModelEventType,
    ModelEvent,
    ModelSessionCreated,
    ModelSessionUpdated,
    ModelResponseCreated,
    ModelResponseAudioDelta,
    ModelResponseAudioDone,
    ModelResponseAudioTranscriptDelta,
    ModelResponseAudioTranscriptDone,
    ModelResponseToolUseDelta,
    ModelResponseToolUseDone,
    ModelResponseDone,
    ModelInputTranscriptionDelta,
    ModelInputTranscriptionDone,
    ModelInputStarted,
    ModelInputDone,
    ModelError,
    ModelWebSocketConnect,
    ModelWebSocketDisconnect,
    # Agent Event Types
    AgentEventType,
    AgentEvent,
    AgentSessionCreated,
    AgentSessionUpdated,
    AgentResponseCreated,
    AgentResponseDelta,
    AgentResponseDone,
    AgentInputTranscriptionDelta,
    AgentInputTranscriptionDone,
    AgentInputStarted,
    AgentInputDone,
    AgentError,
    # Content Blocks
    TextBlock,
    AudioBlock,
    ToolUseBlock,
    ToolResultBlock,
    ContentBlock,
)

from .model import RealtimeVoiceModelBase, resample_audio
from .model_dashscope import DashScopeRealtimeModel
from .model_gemini import GeminiRealtimeModel
from .model_openai import OpenAIRealtimeModel
from .agent import RealtimeVoiceAgent
from .msg_stream import EventMsgStream

__all__ = [
    # Model
    "RealtimeVoiceModelBase",
    "DashScopeRealtimeModel",
    "GeminiRealtimeModel",
    "OpenAIRealtimeModel",
    # Utilities
    "resample_audio",
    # Agent
    "RealtimeVoiceAgent",
    # MsgStream
    "EventMsgStream",
    # Model Events
    "ModelEventType",
    "ModelEvent",
    "ModelSessionCreated",
    "ModelSessionUpdated",
    "ModelResponseCreated",
    "ModelResponseAudioDelta",
    "ModelResponseAudioDone",
    "ModelResponseAudioTranscriptDelta",
    "ModelResponseAudioTranscriptDone",
    "ModelResponseToolUseDelta",
    "ModelResponseToolUseDone",
    "ModelResponseDone",
    "ModelInputTranscriptionDelta",
    "ModelInputTranscriptionDone",
    "ModelInputStarted",
    "ModelInputDone",
    "ModelError",
    "ModelWebSocketConnect",
    "ModelWebSocketDisconnect",
    # Agent Events
    "AgentEventType",
    "AgentEvent",
    "AgentSessionCreated",
    "AgentSessionUpdated",
    "AgentResponseCreated",
    "AgentResponseDelta",
    "AgentResponseDone",
    "AgentInputTranscriptionDelta",
    "AgentInputTranscriptionDone",
    "AgentInputStarted",
    "AgentInputDone",
    "AgentError",
    # Content Blocks
    "TextBlock",
    "AudioBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ContentBlock",
]
