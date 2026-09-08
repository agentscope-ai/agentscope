# -*- coding: utf-8 -*-
"""Realtime voice sessions in AgentScope.

A voice session is a transport (where audio comes from and goes to), a
realtime model (what turns audio into a reply) and the turn-taking state
machine between them. Only the model knows whether the reply came from a
speech-to-speech API or, later, a cascaded chain.
"""
from ._agent import RealtimeAgent
from ._aggregator import TurnAggregator
from ._base import RealtimeModelBase, TruncationSupport
from ._dashscope import DashScopeRealtimeModel
from ._events import (
    AudioDelta,
    InputTranscription,
    ModelError,
    ModelEvent,
    ResponseCreated,
    ResponseDone,
    SessionEnded,
    SpeechEnded,
    SpeechStarted,
    ToolCall,
    TranscriptDelta,
)
from ._metrics import TurnMetrics
from ._model_card import RealtimeModelCard
from ._playout import PlayoutPosition
from ._transport import (
    AudioFrame,
    ControlFrame,
    ControlFrameType,
    LocalAudioTransport,
    TransportBase,
    TransportFrame,
)
from ._vad import SpeechEvent, VADBase

__all__ = [
    # Agent
    "RealtimeAgent",
    "TurnAggregator",
    # Model
    "RealtimeModelBase",
    "DashScopeRealtimeModel",
    "RealtimeModelCard",
    "TruncationSupport",
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
    "SpeechEvent",
    # Metrics
    "TurnMetrics",
    # Model events
    "ModelEvent",
    "SessionEnded",
    "SpeechStarted",
    "SpeechEnded",
    "InputTranscription",
    "ResponseCreated",
    "AudioDelta",
    "TranscriptDelta",
    "ToolCall",
    "ResponseDone",
    "ModelError",
]
