# -*- coding: utf-8 -*-
"""The model usage class in agentscope."""
from dataclasses import dataclass, field
from typing import Literal

from .._utils._mixin import DictMixin


@dataclass(init=False)
class ChatUsage(DictMixin):
    """The usage of a chat model API invocation."""

    input_tokens: int
    """The number of input tokens."""

    output_tokens: int
    """The number of output tokens."""

    time: float
    """The time used in seconds."""

    type: Literal["chat"]
    """The type of the usage, must be `chat`."""

    def __init__(self,
                input_tokens: int = 0,
                output_tokens: int = 0,
                time: float = 0,
                **kwargs):
        super().__init__(input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        time = time,
                        **kwargs)
        self.type = "chat"  

    def __repr__(self):
        base_info = (f"ChatUsage("
                     f"input_tokens={self.input_tokens}, "
                     f"output_tokens={self.output_tokens}, "
                     f"time={self.time:.2f}, "
                     f"type='{self.type}'")
        extra = {k: v for k, v in self.items() if k not in ["input_tokens", "output_tokens", "time", "type"]}
        if extra:
            extra_str = ", ".join(f"{k}={v!r}" for k, v in extra.items())
            base_info += f", {extra_str}"
        base_info += ")"
        return base_info 
