# -*- coding: utf-8 -*-
"""The DashScope TTS module."""

from ._model import DashScopeTTSModel
from ._realtime_model import DashScopeRealtimeTTSModel
from ._cosyvoice_realtime_model import DashScopeCosyVoiceRealtimeTTSModel

__all__ = [
    "DashScopeTTSModel",
    "DashScopeRealtimeTTSModel",
    "DashScopeCosyVoiceRealtimeTTSModel",
]
