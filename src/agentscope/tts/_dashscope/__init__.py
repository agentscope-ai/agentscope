# -*- coding: utf-8 -*-
"""The DashScope TTS module."""

from ._cosyvoice_model import DashScopeCosyVoiceTTSModel
from ._model import DashScopeTTSModel
from ._realtime_model import DashScopeRealtimeTTSModel
from ._voice_cloning_client import DashScopeVoiceCloningClient

__all__ = [
    "DashScopeCosyVoiceTTSModel",
    "DashScopeTTSModel",
    "DashScopeRealtimeTTSModel",
    "DashScopeVoiceCloningClient",
]
