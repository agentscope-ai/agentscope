# -*- coding: utf-8 -*-
"""The model usage class in agentscope."""
from dataclasses import dataclass, field
from typing import Literal, Any

from .._utils._mixin import DictMixin


def _to_usage_dict(obj: Any) -> dict[str, Any]:
    """Convert an arbitrary usage object to a plain dict, best-effort.

    Handles the following common SDK types in order of preference:
    - Already a ``dict`` — returned as-is.
    - Pydantic v2 models (``model_dump``).
    - Pydantic v1 models (``dict``).
    - Protobuf messages with an instance ``to_dict`` method.
    - Any Python object that exposes ``__dict__`` (public fields only).
    - Fallback: ``{"_raw": str(obj)}``.
    """
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return obj.model_dump()
    if hasattr(obj, "dict") and callable(obj.dict):
        return obj.dict()
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        result = obj.to_dict()
        if isinstance(result, dict):
            return result
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {"_raw": str(obj)}


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

    metadata: dict[str, Any] | None = field(default_factory=lambda: None)
    """The metadata of the usage, stored as a plain dict."""
