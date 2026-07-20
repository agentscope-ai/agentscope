# -*- coding: utf-8 -*-
"""The TTS response module."""
from dataclasses import dataclass, field
from typing import Literal

from .._utils._common import _get_timestamp
from .._utils._mixin import DictMixin
from ..message import DataBlock
from ..types import JSONSerializableObject


@dataclass
class TTSUsage(DictMixin):
    """The usage of a TTS model API invocation."""

    input_tokens: int
    """The number of input tokens."""

    output_tokens: int
    """The number of output tokens."""

    time: float
    """The time used in seconds."""

    type: Literal["tts"] = field(default_factory=lambda: "tts")
    """The type of the usage, must be `tts`."""


@dataclass
class TTSResponse(DictMixin):
    """The response of TTS models."""

    content: DataBlock | None
    """The audio chunk produced by the TTS model. The audio format is
    indicated by ``content.source.media_type`` (e.g.
    ``"audio/pcm;rate=24000"``, ``"audio/mpeg"``)."""

    id: str = field(default_factory=lambda: _get_timestamp(True))
    """The unique identifier of the response."""

    created_at: str = field(default_factory=_get_timestamp)
    """When the response was created."""

    type: Literal["tts"] = field(default_factory=lambda: "tts")
    """The type of the response, which is always 'tts'."""

    usage: TTSUsage | None = field(default_factory=lambda: None)
    """The usage information of the TTS response, if available."""

    metadata: dict[str, JSONSerializableObject] | None = field(
        default_factory=lambda: None,
    )
    """The metadata of the TTS response."""

    is_last: bool = field(default_factory=lambda: True)
    """Whether this is the last response in a stream of TTS responses."""
