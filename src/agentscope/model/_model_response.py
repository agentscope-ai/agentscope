# -*- coding: utf-8 -*-
"""The model response module."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self, List

from ._model_usage import ChatUsage
from .._utils._common import _generate_id
from .._utils._mixin import DictMixin
from ..message import (
    TextBlock,
    ToolCallBlock,
    ThinkingBlock,
    DataBlock,
)
from ..types import JSONSerializableObject


class FinishedReason(StrEnum):
    """The finished reason of the model response."""

    INTERRUPTED = "interrupted"
    """The model response is interrupted by the asyncio.CancelError."""

    COMPLETED = "completed"
    """The model response is completed."""


@dataclass
class ChatResponse(DictMixin):
    """The response of chat models."""

    content: List[TextBlock | ToolCallBlock | ThinkingBlock | DataBlock]
    """The content of the chat response, which can include text blocks,
    tool use blocks, or thinking blocks."""

    is_last: bool
    """Whether this response is the last response, if `Ture`, the content will
    be the complete response, otherwise the content is a partial response"""

    id: str = field(default_factory=_generate_id)
    """The unique identifier."""

    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    """When the response was created"""

    type: Literal["chat_response"] = field(
        default_factory=lambda: "chat_response",
    )
    """The type of the response, which is always 'chat_response'."""

    usage: ChatUsage | None = field(default_factory=lambda: None)
    """The usage information of the chat response, if available."""

    finished_reason: FinishedReason = field(
        default=FinishedReason.COMPLETED,
    )
    """The finished reason of the chat response, available when `is_last`
    is `True`."""

    metadata: dict[str, JSONSerializableObject] = field(
        default_factory=lambda: {},
    )
    """The metadata of the chat response"""

    def append_text(self, text: str, block_id: str | None = None) -> Self:
        """Append text to the current response."""
        for block in self.content:
            if isinstance(block, TextBlock) and (
                block_id is None or block_id == block.id
            ):
                block.text += text
                return self

        # Append a new block
        assert isinstance(self.content, list)
        self.content.append(
            TextBlock(text=text, id=block_id or _generate_id()),
        )
        return self

    def append_thinking(
        self,
        thinking: str,
        block_id: str | None = None,
        signature: str | None = None,
    ) -> Self:
        """Append thinking to the current response."""
        for block in self.content:
            if isinstance(block, ThinkingBlock) and (
                block_id is None or block_id == block.id
            ):
                block.thinking += thinking
                if signature:
                    block.signature = signature
                return self

        assert isinstance(self.content, list)
        block = ThinkingBlock(thinking=thinking, id=block_id or _generate_id())
        if signature:
            block.signature = signature
        self.content.append(block)
        return self

    def append_tool_call(
        self,
        block_id: str,
        name: str,
        input: str,  # pylint: disable=redefined-builtin
    ) -> Self:
        """Append tool call to the current response by tool call block ID."""
        for block in self.content:
            if isinstance(block, ToolCallBlock) and block.id == block_id:
                block.input += input
                return self

        block = ToolCallBlock(
            id=block_id,
            name=name,
            input=input,
        )
        assert isinstance(self.content, list)
        self.content.append(block)
        return self

    def append_chat_response(self, chat_response: Self) -> Self:
        """Append chat response to the current response."""
        # Append content
        new_block_dict = {_.id: _ for _ in chat_response.content}
        for block in self.content:
            if block.id in new_block_dict:
                delta_block = new_block_dict.pop(block.id)
                # Append data according to the block type
                if isinstance(block, ThinkingBlock):
                    block.thinking += delta_block.thinking
                    # Anthropic API requires additional signature field
                    if getattr(delta_block, "signature", None):
                        block.signature = delta_block.signature

                elif isinstance(block, TextBlock):
                    block.text += delta_block.text

                elif isinstance(block, ToolCallBlock):
                    block.input += delta_block.input

                elif isinstance(block, DataBlock):
                    # TODO: handling data block then
                    pass

        if new_block_dict:
            # Attach new blocks to the content
            self.content.extend(new_block_dict.values())

        # Override the chat usage
        if chat_response.usage:
            self.usage = chat_response.usage

        return self


@dataclass
class StructuredResponse:
    """The structured response of chat models."""

    content: dict
    """The structured output of the model."""

    id: str = field(default_factory=_generate_id)
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

    finished_reason: FinishedReason = field(
        default=FinishedReason.COMPLETED,
    )
    """The finished reason of the structured response."""
