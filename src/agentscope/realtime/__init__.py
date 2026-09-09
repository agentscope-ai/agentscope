# -*- coding: utf-8 -*-
"""Realtime voice sessions in AgentScope.

The model side of a voice session: realtime models, their cards and
events, transports and VAD. The agent that ties them together is
:class:`agentscope.agent.RealtimeAgent`.
"""
from ._base import ModelDisconnectedError, RealtimeModelBase, TruncationSupport
from ._dashscope import (
    DashScopeAudioRealtimeModel,
    DashScopeRealtimeModel,
)
from ._events import (
    AudioDeltaEvent,
    InputTranscriptionEvent,
    ModelErrorEvent,
    ModelEvent,
    ResponseCreatedEvent,
    ResponseDoneEvent,
    SessionEndedEvent,
    SpeechEndedEvent,
    SpeechStartedEvent,
    ToolCallEvent,
    TranscriptDeltaEvent,
)
from ._model_card import RealtimeModelCard
from ._openai import OpenAIRealtimeModel
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
    "DashScopeRealtimeModel",
    "DashScopeAudioRealtimeModel",
    "OpenAIRealtimeModel",
    "XAIRealtimeModel",
    "RealtimeModelCard",
    "TruncationSupport",
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
    "SpeechStartedEvent",
    "SpeechEndedEvent",
    "InputTranscriptionEvent",
    "ResponseCreatedEvent",
    "AudioDeltaEvent",
    "TranscriptDeltaEvent",
    "ToolCallEvent",
    "ResponseDoneEvent",
    "ModelErrorEvent",
]
