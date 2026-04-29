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
    """The unique identifier."""

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    """When the response was created"""

    type: Literal["chat_response"] = field(
        default_factory=lambda: "chat_response",
    )
    """The type of the response, which is always 'chat_response'."""

    usage: ChatUsage | None = field(default_factory=lambda: None)
    """The usage information of the chat response, if available."""

    metadata: dict[str, JSONSerializableObject] = field(
        default_factory=lambda: {},
    )
    """The metadata of the chat response"""


@dataclass
class StructuredResponse:
    """The structured response of chat models."""

    content: dict
    """The structured output of the model."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    """The unique identifier."""

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    """When the response was created"""

    type: Literal["structured_response"] = field(
        default_factory=lambda: "structured_response",
    )
    """The type of the response, which is always 'structured_response'."""

    usage: ChatUsage | None = field(default_factory=lambda: None)
    """The usage information of the chat response, if available."""

    metadata: dict[str, JSONSerializableObject] = field(
        default_factory=lambda: {},
    )
    """The metadata of the chat response"""
