# -*- coding: utf-8 -*-
"""The model response module."""
import base64
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self, List

from ._model_usage import ChatUsage
from .._utils._common import _generate_id
from .._utils._mixin import DictMixin
from ..message import (
    TextBlock,
    ToolCallBlock,
    ThinkingBlock,
    DataBlock,
    Base64Source,
)
from ..types import JSONSerializableObject


class FinishedReason(StrEnum):
    """The finished reason of the model response."""

    INTERRUPTED = "interrupted"
    """The model response is interrupted by the asyncio.CancelledError."""

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
        **extra_fields: Any,
    ) -> Self:
        """Append thinking to the current response.

        Args:
            thinking (`str`):
                The thinking content to append.
            block_id (`str | None`, defaults to `None`):
                The id of the ``ThinkingBlock`` to accumulate into. If no
                matching block exists in ``content``, a new one is
                appended.
            **extra_fields (`Any`):
                Additional provider-specific fields to attach to the
                ``ThinkingBlock`` (e.g. Anthropic's ``signature``, OpenAI
                Responses API's ``reasoning_item_id``). Only non-``None``
                values are applied.
        """
        for block in self.content:
            if isinstance(block, ThinkingBlock) and (
                block_id is None or block_id == block.id
            ):
                block.thinking += thinking
                for key, value in extra_fields.items():
                    if value is not None:
                        setattr(block, key, value)
                return self

        assert isinstance(self.content, list)
        block = ThinkingBlock(thinking=thinking, id=block_id or _generate_id())
        for key, value in extra_fields.items():
            if value is not None:
                setattr(block, key, value)
        self.content.append(block)
        return self

    def append_tool_call(
        self,
        block_id: str,
        name: str,
        input: str,  # pylint: disable=redefined-builtin
        **extra_fields: Any,
    ) -> Self:
        """Append tool call to the current response by tool call block ID.

        Args:
            block_id (`str`):
                The id of the ``ToolCallBlock`` to accumulate into. If no
                matching block exists in ``content``, a new one is
                appended.
            name (`str`):
                The name of the tool being called.
            input (`str`):
                The incremental JSON string arguments to append.
            **extra_fields (`Any`):
                Additional provider-specific fields to attach to the
                ``ToolCallBlock`` (e.g. OpenAI Responses API's
                ``call_id``). Only non-``None`` values are applied.
        """
        for block in self.content:
            if isinstance(block, ToolCallBlock) and block.id == block_id:
                block.input += input
                for key, value in extra_fields.items():
                    if value is not None:
                        setattr(block, key, value)
                return self

        block = ToolCallBlock(
            id=block_id,
            name=name,
            input=input,
        )
        for key, value in extra_fields.items():
            if value is not None:
                setattr(block, key, value)
        assert isinstance(self.content, list)
        self.content.append(block)
        return self

    def append_data_block(
        self,
        block_id: str,
        data: bytes,
        media_type: str,
        name: str | None = None,
    ) -> Self:
        """Append raw media bytes to the ``DataBlock`` with the given id.

        The accumulated bytes are stored base64-encoded in
        ``DataBlock.source.data`` (via a :class:`Base64Source`). Callers must
        pass the *incremental* raw media bytes only — the method takes care
        of base64 (de)coding internally, so consumers never have to worry
        about base64 padding (``=``) corrupting concatenation.

        .. note::

            Byte-level delta accumulation only has a well-defined semantics
            for streaming media where chunks can be safely concatenated
            (currently ``audio/*``). For non-streamable media types
            (e.g. ``image/*``, ``video/*``) each ``DataBlock`` should be
            treated as a complete file and passed atomically rather than
            accumulated through this method.

        Args:
            block_id (`str`):
                The id of the ``DataBlock`` to accumulate into. If no
                matching block exists in ``content``, a new one is
                appended.
            data (`bytes`):
                The incremental raw media bytes to append.
            media_type (`str`):
                The IANA media type of the raw bytes (e.g. ``audio/pcm``,
                ``audio/wav``). Used both to tag a newly-created block and
                to guard against accidentally mixing bytes from different
                media types under the same id.
            name (`str | None`, defaults to `None`):
                The optional ``name`` field used when a new ``DataBlock``
                needs to be created. Ignored when an existing block with
                ``block_id`` is found.

        Returns:
            `Self`:
                The current ``ChatResponse`` instance for chaining.
        """
        for block in self.content:
            if (
                isinstance(block, DataBlock)
                and block.id == block_id
                and isinstance(block.source, Base64Source)
                and block.source.media_type == media_type
            ):
                old_bytes = (
                    base64.b64decode(block.source.data)
                    if block.source.data
                    else b""
                )
                block.source.data = base64.b64encode(
                    old_bytes + data,
                ).decode("ascii")
                return self

        self.content.append(
            DataBlock(
                id=block_id,
                source=Base64Source(
                    data=base64.b64encode(data).decode("ascii"),
                    media_type=media_type,
                ),
                name=name,
            ),
        )
        return self

    def append_chat_response(self, chat_response: Self) -> Self:
        """Append chat response to the current response.

        .. deprecated::
            Internal streaming accumulation now uses fragment-based
            collection (see ``ChatModelBase._collect_chunk_fragments``
            and ``ChatModelBase._build_acc_response``) which avoids
            O(n^2) string concatenation. This method is retained for
            backward compatibility with external consumers but is no
            longer called by AgentScope's own streaming paths.
        """
        # Append content
        new_block_dict = {_.id: _ for _ in chat_response.content}
        for block in self.content:
            if block.id in new_block_dict:
                delta_block = new_block_dict.pop(block.id)
                # Append data according to the block type
                if isinstance(block, ThinkingBlock):
                    block.thinking += delta_block.thinking
                    # Provider-specific extra fields (e.g. Anthropic's
                    # ``signature``, OpenAI Responses API's
                    # ``reasoning_item_id``) are carried on the delta
                    # ``ThinkingBlock`` via pydantic's ``extra="allow"``.
                    # Copy any non-``None`` extras onto the accumulator.
                    for key, value in (delta_block.model_extra or {}).items():
                        if value is not None:
                            setattr(block, key, value)

                elif isinstance(block, TextBlock):
                    block.text += delta_block.text

                elif isinstance(block, ToolCallBlock):
                    block.input += delta_block.input
                    # Provider-specific extras (e.g. OpenAI Responses
                    # API's ``call_id``) may be attached on the delta
                    # block via pydantic's ``extra="allow"``.
                    for key, value in (delta_block.model_extra or {}).items():
                        if value is not None:
                            setattr(block, key, value)

                elif isinstance(block, DataBlock):
                    # Only ``audio/*`` is treated as a streamable delta:
                    # callers accumulate raw media bytes across chunks and
                    # the concatenated result is a well-defined stream.
                    # For non-audio media types (``image/*``, ``video/*``,
                    # ...) each ``DataBlock`` is a complete standalone
                    # asset — byte concatenation is meaningless — so we
                    # overwrite in place with the latest delta instead.
                    if not (
                        isinstance(block.source, Base64Source)
                        and isinstance(delta_block.source, Base64Source)
                        and block.source.media_type
                        == delta_block.source.media_type
                    ):
                        # Source shape / media type mismatch: replace the
                        # whole block to avoid mixing incompatible data.
                        block.source = delta_block.source
                    elif block.source.media_type.startswith("audio/"):
                        old_bytes = (
                            base64.b64decode(block.source.data)
                            if block.source.data
                            else b""
                        )
                        delta_bytes = (
                            base64.b64decode(delta_block.source.data)
                            if delta_block.source.data
                            else b""
                        )
                        block.source.data = base64.b64encode(
                            old_bytes + delta_bytes,
                        ).decode("ascii")
                    else:
                        # Non-streamable media: latest delta wins.
                        block.source.data = delta_block.source.data

        if new_block_dict:
            # Attach new blocks to the content.
            self.content.extend(
                block.model_copy(deep=True)
                for block in new_block_dict.values()
            )

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
