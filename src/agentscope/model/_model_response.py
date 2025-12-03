# -*- coding: utf-8 -*-
"""The model response module."""

from dataclasses import dataclass, field
from typing import Literal, Sequence

from ._model_usage import ChatUsage
from .._utils._common import _get_timestamp
from .._utils._mixin import DictMixin
from ..message import (
    TextBlock,
    ToolUseBlock,
    ThinkingBlock,
    AudioBlock,
)
from ..types import JSONSerializableObject


@dataclass
class ChatResponse(DictMixin):
    """The response of chat models."""

    content: Sequence[TextBlock | ToolUseBlock | ThinkingBlock | AudioBlock]
    """The content of the chat response, which can include text blocks,
    tool use blocks, or thinking blocks."""

    id: str = field(default_factory=lambda: _get_timestamp(True))
    """The unique identifier formatter """

    created_at: str = field(default_factory=_get_timestamp)
    """When the response was created"""

    type: Literal["chat"] = field(default_factory=lambda: "chat")
    """The type of the response, which is always 'chat'."""

    usage: ChatUsage | None = field(default_factory=lambda: None)
    """The usage information of the chat response, if available."""

    metadata: dict[str, JSONSerializableObject] | None = field(
        default_factory=lambda: None,
    )
    """The metadata of the chat response"""
