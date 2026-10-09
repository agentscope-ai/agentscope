# -*- coding: utf-8 -*-
"""Speech recognition response."""

from dataclasses import dataclass, field
from typing import Literal

from .._utils._common import _generate_timestamp, _get_timestamp
from .._utils._mixin import DictMixin
from ..message import TextBlock
from ..types import JSONSerializableObject


@dataclass
class ASRResponse(DictMixin):
    """The text produced by an ASR model."""

    content: TextBlock
    """The transcribed text content."""

    id: str = field(default_factory=lambda: _get_timestamp(True))
    """The unique identifier of the response."""

    created_at: str = field(default_factory=_generate_timestamp)
    """The timestamp when the response was created."""

    type: Literal["asr"] = "asr"
    """The response type discriminator."""

    metadata: dict[str, JSONSerializableObject] | None = None
    """Optional provider-specific response metadata."""
