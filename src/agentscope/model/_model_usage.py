# -*- coding: utf-8 -*-
"""The model usage class in agentscope."""
from dataclasses import dataclass, field
from typing import Literal, Any

from .._utils._mixin import DictMixin


@dataclass
class ChatUsage(DictMixin):
    """The usage of a chat model API invocation."""

    input_tokens: int
    """The number of input tokens."""

    output_tokens: int
    """The number of output tokens."""

    time: float
    """The time used in seconds."""

    type: Literal["chat"] = field(default_factory=lambda: "chat")
    """The type of the usage, must be `chat`."""

    metadata: dict[str, Any] | None = field(default=None)
    """The metadata of the usage."""
