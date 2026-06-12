# -*- coding: utf-8 -*-
"""The DashScope TTS module."""

from ._cosyvoice_model import DashScopeCosyVoiceTTSModel
from ._model import DashScopeTTSModel
from ._realtime_model import DashScopeRealtimeTTSModel
from ._cosyvoice_realtime_model import DashScopeCosyVoiceRealtimeTTSModel

__all__ = [
    "DashScopeCosyVoiceTTSModel",
    "DashScopeTTSModel",
    "DashScopeRealtimeTTSModel",
    "DashScopeCosyVoiceRealtimeTTSModel",
]
