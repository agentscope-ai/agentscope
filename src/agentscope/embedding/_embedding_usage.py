# -*- coding: utf-8 -*-
"""The embedding usage class in agentscope."""
from dataclasses import dataclass, field
from typing import Literal

from .._utils._mixin import DictMixin


@dataclass
class EmbeddingUsage(DictMixin):
    """The usage of an embedding model API invocation."""

    time: float
    """The time used in seconds."""

    tokens: int | None = field(default_factory=lambda: None)
    """The number of tokens used, if available."""

    type: Literal["embedding"] = field(default_factory=lambda: "embedding")
    """The type of the usage, must be `embedding`."""
