# -*- coding: utf-8 -*-
"""The realtime voice models."""

from ._voice_model_base import VoiceModelBase
from ._dashscope_voice_model import DashScopeVoiceModel


__all__ = [
    "VoiceModelBase",
    "DashScopeVoiceModel",
]
