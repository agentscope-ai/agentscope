# -*- coding: utf-8 -*-
"""The TTS (Text-to-Speech) module."""

from ._tts_base import TTSModelBase
from ._tts_response import TTSResponse, TTSUsage
from ._dashscope_tts_model import DashScopeTTSModel
from ._dashscope_realtime_tts_model import DashScopeRealtimeTTSModel
from ._gemini_tts_model import GeminiTTSModel
from ._openai_tts_model import OpenAITTSModel
from ._dashscope_cosyvoice_tts_model import DashScopeCosyVoiceTTSModel
from ._dashscope_cosyvoice_realtime_tts_model import (
    DashScopeCosyVoiceRealtimeTTSModel,
)

__all__ = [
    "TTSModelBase",
    "TTSResponse",
    "TTSUsage",
    "DashScopeTTSModel",
    "DashScopeRealtimeTTSModel",
    "GeminiTTSModel",
    "OpenAITTSModel",
    "DashScopeCosyVoiceTTSModel",
    "DashScopeCosyVoiceRealtimeTTSModel",
]
