# -*- coding: utf-8 -*-
"""The utils for realtime events."""
from pydantic import BaseModel, ConfigDict


class AudioFormat(BaseModel):
    """The audio format descriptor for realtime audio chunks."""

    model_config = ConfigDict(extra="allow")

    type: str
    """The audio type, e.g., ``'audio/pcm'``."""

    rate: int
    """The audio sample rate in Hz, e.g., ``16000`` or ``24000``."""
