# -*- coding: utf-8 -*-
"""The model response module."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Sequence

from ._model_usage import ChatUsage
from .._utils._mixin import DictMixin
from ..message import (
    TextBlock,
    ToolCallBlock,
    ThinkingBlock,
    DataBlock,
)
from ..types import JSONSerializableObject


@dataclass
class ChatResponse(DictMixin):
    """The response of chat models."""

    content: Sequence[TextBlock | ToolCallBlock | ThinkingBlock | DataBlock]
    """The content of the chat response, which can include text blocks,
    tool use blocks, or thinking blocks."""

    is_last: bool
    """Whether this response is the last response, if `Ture`, the content will
    be the complete response, otherwise the content is a partial response"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    """The unique identifier formatter """

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    """When the response was created"""

    type: Literal["chat"] = field(default_factory=lambda: "chat")
    """The type of the response, which is always 'chat'."""

    usage: ChatUsage | None = field(default_factory=lambda: None)
    """The usage information of the chat response, if available."""

    metadata: dict[str, JSONSerializableObject] | None = field(
        default_factory=lambda: None,
    )
    """The metadata of the chat response"""
