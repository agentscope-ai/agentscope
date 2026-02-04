# -*- coding: utf-8 -*-
"""The utils for realtime events."""
from pydantic import BaseModel, ConfigDict


class AudioFormat(BaseModel):
    """The audio format class"""

    model_config = ConfigDict(extra="allow")

    type: str
    """The audio type, e.g., 'audio/pcm'"""

    rate: int
    """The audio sample rate, e.g., 16000"""
