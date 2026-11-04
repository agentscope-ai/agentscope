# -*- coding: utf-8 -*-
"""The OpenAI TTS module."""

from ._model import OpenAITTSModel
from ._voice_cloning_client import OpenAIVoiceCloningClient

__all__ = [
    "OpenAITTSModel",
    "OpenAIVoiceCloningClient",
]
