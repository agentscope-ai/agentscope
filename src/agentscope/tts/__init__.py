# -*- coding: utf-8 -*-
"""The TTS (Text-to-Speech) module in AgentScope."""

from ._tts_base import TTSModelBase
from ._tts_model_card import TTSModelCard
from ._tts_response import TTSResponse, TTSUsage
from ._dashscope import (
    DashScopeCosyVoiceTTSModel,
    DashScopeTTSModel,
    DashScopeRealtimeTTSModel,
)
from ._gemini import GeminiTTSModel
from ._openai import OpenAITTSModel

__all__ = [
    "TTSModelBase",
    "TTSModelCard",
    "TTSResponse",
    "TTSUsage",
    "DashScopeCosyVoiceTTSModel",
    "DashScopeTTSModel",
    "DashScopeRealtimeTTSModel",
    "GeminiTTSModel",
    "OpenAITTSModel",
]
