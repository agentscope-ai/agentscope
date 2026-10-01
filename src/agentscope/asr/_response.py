# -*- coding: utf-8 -*-
"""Speech recognition response."""

from dataclasses import dataclass, field
from typing import Literal

from .._utils._common import _get_timestamp
from .._utils._mixin import DictMixin
from ..message import TextBlock
from ..types import JSONSerializableObject


@dataclass
class ASRResponse(DictMixin):
    """The text produced by an ASR model."""

    content: TextBlock
    id: str = field(default_factory=lambda: _get_timestamp(True))
    created_at: str = field(default_factory=_get_timestamp)
    type: Literal["asr"] = "asr"
    metadata: dict[str, JSONSerializableObject] | None = None
