# -*- coding: utf-8 -*-
"""The model usage class in agentscope."""
from dataclasses import dataclass, field
from typing import Literal

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

    prompt_tokens_details: dict = field(default_factory=dict)
    """The details of cached tokens. """

    type: Literal["chat"] = field(default_factory=lambda: "chat")
    """The type of the usage, must be `chat`."""
