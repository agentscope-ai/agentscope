# -*- coding: utf-8 -*-
"""Usage information for classifier model calls."""
from dataclasses import dataclass, field
from typing import Literal

from .._utils._mixin import DictMixin


@dataclass
class ClassifierUsage(DictMixin):
    """The usage of a classifier model API invocation."""

    time: float
    """The wall-clock time used in seconds."""

    input_tokens: int | None = field(default_factory=lambda: None)
    """The number of input tokens, if reported by the provider."""

    output_tokens: int | None = field(default_factory=lambda: None)
    """The number of output tokens, if reported by the provider."""

    type: Literal["classifier"] = field(
        default_factory=lambda: "classifier",
    )
    """The usage type discriminator."""
