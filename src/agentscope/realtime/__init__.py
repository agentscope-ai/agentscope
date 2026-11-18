# -*- coding: utf-8 -*-
"""Realtime voice sessions in AgentScope.

The model side of a voice session: realtime models, their cards and
events, transports and VAD. The agent that ties them together is
:class:`agentscope.agent.RealtimeAgent`.
"""
from ._base import InteractionMode, ModelDisconnectedError, RealtimeModelBase
from ._dashscope import (
    DashScopeAudioRealtimeModel,
    DashScopeRealtimeModel,
)
from ._events import (
    AudioDeltaEvent,
    DelegatedResponseEvent,
    DelegationCreatedEvent,
    InputTranscriptionEvent,
    InputTranscriptionFailedEvent,
    ModelErrorEvent,
    ModelEvent,
    ResponseCreatedEvent,
    ResponseDoneEvent,
    SessionClosedEvent,
    SessionEndedEvent,
    SessionStartedEvent,
    SessionUsageEvent,
    SpeechEndedEvent,
    SpeechStartedEvent,
    TimelineAudioDeltaEvent,
    TimelineTranscriptDeltaEvent,
    ToolCallEvent,
    TranscriptDeltaEvent,
)
from ._gemini import GeminiRealtimeModel
from ._model_card import RealtimeModelCard
from ._openai import OpenAIRealtimeModel
from ._openai_live import OpenAILiveModel
from ._playout import PlayoutPosition
from ._transport import (
    AudioFrame,
    ControlFrame,
    ControlFrameType,
    LocalAudioTransport,
    TransportBase,
    TransportFrame,
)
from ._vad import SpeechTransition, VADBase
from ._xai import XAIRealtimeModel

__all__ = [
    # Model
    "RealtimeModelBase",
    "InteractionMode",
    "DashScopeRealtimeModel",
    "DashScopeAudioRealtimeModel",
    "GeminiRealtimeModel",
    "OpenAIRealtimeModel",
    "OpenAILiveModel",
    "XAIRealtimeModel",
    "RealtimeModelCard",
    "ModelDisconnectedError",
    # Transport
    "TransportBase",
    "LocalAudioTransport",
    "TransportFrame",
    "AudioFrame",
    "ControlFrame",
    "ControlFrameType",
    "PlayoutPosition",
    # Turn taking
    "VADBase",
    "SpeechTransition",
    # Model events
    "ModelEvent",
    "SessionEndedEvent",
    "SessionStartedEvent",
    "SessionClosedEvent",
    "SessionUsageEvent",
    "SpeechStartedEvent",
    "SpeechEndedEvent",
    "InputTranscriptionEvent",
    "InputTranscriptionFailedEvent",
    "ResponseCreatedEvent",
    "AudioDeltaEvent",
    "TimelineAudioDeltaEvent",
    "TranscriptDeltaEvent",
    "TimelineTranscriptDeltaEvent",
    "DelegationCreatedEvent",
    "DelegatedResponseEvent",
    "ToolCallEvent",
    "ResponseDoneEvent",
    "ModelErrorEvent",
]
