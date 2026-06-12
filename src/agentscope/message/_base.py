# -*- coding: utf-8 -*-
"""The message class in agentscope."""
import base64
import uuid
from datetime import datetime
from typing import Literal, List, overload, Sequence, Self, TYPE_CHECKING, Any

from pydantic import BaseModel, Field, model_validator

from ._block import (
    TextBlock,
    ThinkingBlock,
    HintBlock,
    DataBlock,
    Base64Source,
    URLSource,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
    ToolResultState,
    ContentBlock,
    ContentBlockTypes,
)
from .._logging import logger

if TYPE_CHECKING:
    from ..event import AgentEvent
else:
    AgentEvent = Any


def _assert_user_content_blocks(content: Sequence[ContentBlock]) -> None:
    """Assert that the content blocks in user message are valid."""
    for block in content:
        if block.type not in ["text", "data"]:
            raise ValueError(
                "User message can only contain text blocks or data blocks.",
            )


def _assert_system_content_blocks(
    content: Sequence[ContentBlock],
) -> None:
    """Assert that the content blocks in system message are valid."""
    for block in content:
        if block.type not in ["text"]:
            raise ValueError("System message can only contain text blocks.")


def _to_blocks(content: str | list) -> list:
    """Convert a plain string to a single-element TextBlock list."""
    if isinstance(content, str):
        return [TextBlock(text=content)]
    return content


class Usage(BaseModel):
    """The token usage information of a message."""

    input_tokens: int
    """The number of input tokens."""
    output_tokens: int
    """The number of output tokens."""


class Msg(BaseModel):
    """The message class in AgentScope, responsible for information storage
    and transmission among different agents."""

    name: str
    """The name of the sender."""
    content: list[ContentBlock]
    """The message content as a list of content blocks."""
    role: Literal["user", "assistant", "system"]
    """The role of the sender."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The message identifier."""
    metadata: dict = Field(default_factory=dict)
    """The metadata of the message"""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    """The creation time of the message"""
    finished_at: str | None = Field(default=None)
    """The finished time of the message"""
    usage: Usage | None = Field(default=None)
    """The token usage information of the message"""

    @model_validator(mode="after")
    def validate_role_content(self) -> Self:
        """Validate content blocks according to the role."""
        match self.role:
            case "user":
                _assert_user_content_blocks(self.content)
            case "system":
                _assert_system_content_blocks(self.content)
            case "assistant":
                pass
        return self

    def has_content_blocks(
        self,
        block_type: ContentBlockTypes | list[ContentBlockTypes] | None = None,
    ) -> bool:
        """Check if the message has content blocks of the given type.

        Args:
            block_type (`ContentBlockTypes | list[ContentBlockTypes] | None`, \
            optional):
                The type of the block to be checked. If `None`, all blocks will
                be checked. If a list is provided, it checks if there are
                blocks of any types in the list.

        Returns:
            `bool`:
                `True` if there are content blocks of the given type, `False`
                otherwise.
        """
        if block_type is None:
            return len(self.content) > 0

        typs = [block_type] if isinstance(block_type, str) else block_type
        return any(b.type in typs for b in self.content)

    def get_text_content(self, separator: str = "\n") -> str | None:
        """Get the concatenated text from all TextBlocks."""
        gathered = [b.text for b in self.content if b.type == "text"]
        return separator.join(gathered) if gathered else None

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["text"],
    ) -> list[TextBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["thinking"],
    ) -> list[ThinkingBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["tool_call"],
    ) -> list[ToolCallBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["tool_result"],
    ) -> list[ToolResultBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["data"],
    ) -> list[DataBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: None = None,
    ) -> list[ContentBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["hint"],
    ) -> list[HintBlock]:
        ...

    def get_content_blocks(
        self,
        block_type: ContentBlockTypes | List[ContentBlockTypes] | None = None,
    ) -> Sequence[ContentBlock]:
        """Get content blocks, optionally filtered by type.

        Args:
            block_type (`ContentBlockTypes | List[ContentBlockTypes] | None`, \
            optional):
                The type of the block to be extracted. If `None`, all blocks
                will be returned.

        Returns:
            `List[ContentBlock]`:
                The content blocks.
        """
        blocks: list[ContentBlock] = self.content or []
        if isinstance(block_type, str):
            blocks = [b for b in blocks if b.type == block_type]
        elif isinstance(block_type, list):
            blocks = [b for b in blocks if b.type in block_type]
        return blocks

    def _find_block(
        self,
        block_type: str,
        block_id: str,
    ) -> ContentBlock | None:
        """Find a block in content by type and id."""
        for block in self.content:
            if block.type == block_type and block.id == block_id:
                return block
        return None

    def append_event(self, event: AgentEvent) -> Self:
        """Update the message by applying a streaming event.

        Mutates ``self.content``, ``self.finished_at``, and ``self.usage``:
        content blocks are appended/updated by block-level events,
        ``finished_at`` is stamped by ``REPLY_END``, and ``usage`` is
        initialized then accumulated across each ``MODEL_CALL_END``.
        Events whose ``reply_id`` does not match ``self.id`` are skipped with
        a warning. Block-level delta/end events whose target block cannot be
        found are also skipped with a warning.

        Args:
            event (`AgentEvent`):
                The event to apply.
        """
        from ..event import EventType  # local import to avoid circular dep

        if event.reply_id != self.id:
            logger.warning(
                "Event %s with reply_id %r does not match message id %r, "
                "skipping.",
                event.__class__.__name__,
                event.reply_id,
                self.id,
            )
            return self

        match event.type:
            case EventType.REPLY_END:
                self.finished_at = event.created_at

            case EventType.MODEL_CALL_END:
                if self.usage is None:
                    self.usage = Usage(
                        input_tokens=event.input_tokens,
                        output_tokens=event.output_tokens,
                    )
                else:
                    self.usage.input_tokens += event.input_tokens
                    self.usage.output_tokens += event.output_tokens

            case EventType.TEXT_BLOCK_START:
                self.content.append(TextBlock(id=event.block_id, text=""))

            case EventType.TEXT_BLOCK_DELTA:
                block = self._find_block("text", event.block_id)
                if block is None:
                    logger.warning(
                        "TextBlock %r not found, skipping.",
                        event.block_id,
                    )
                else:
                    block.text += event.delta

            case EventType.TEXT_BLOCK_END:
                pass

            case EventType.DATA_BLOCK_START:
                self.content.append(
                    DataBlock(
                        id=event.block_id,
                        source=Base64Source(
                            data="",
                            media_type=event.media_type,
                        ),
                    ),
                )

            case EventType.DATA_BLOCK_DELTA:
                block = self._find_block("data", event.block_id)
                if block is None:
                    logger.warning(
                        "DataBlock %s not found, skipping.",
                        event.block_id,
                    )
                elif event.data:
                    # Each delta is an independently base64-encoded chunk
                    # (with its own padding); naive string concat would
                    # corrupt the byte stream. Decode, concat bytes, re-encode.
                    existing = (
                        base64.b64decode(block.source.data)
                        if block.source.data
                        else b""
                    )
                    incoming = base64.b64decode(event.data)
                    block.source.data = base64.b64encode(
                        existing + incoming,
                    ).decode("ascii")

            case EventType.DATA_BLOCK_END:
                pass

            case EventType.THINKING_BLOCK_START:
                self.content.append(
                    ThinkingBlock(id=event.block_id, thinking=""),
                )

            case EventType.THINKING_BLOCK_DELTA:
                block = self._find_block("thinking", event.block_id)
                if block is None:
                    logger.warning(
                        "ThinkingBlock %r not found, skipping.",
                        event.block_id,
                    )
                else:
                    block.thinking += event.delta

            case EventType.THINKING_BLOCK_END:
                pass

            case EventType.HINT_BLOCK:
                # One-shot event — the full HintBlock content arrives in
                # a single event, so just append it to ``content`` for
                # persistence and replay.
                self.content.append(
                    HintBlock(
                        id=event.block_id,
                        source=event.source,
                        hint=event.hint,
                    ),
                )

            case EventType.TOOL_CALL_START:
                self.content.append(
                    ToolCallBlock(
                        id=event.tool_call_id,
                        name=event.tool_call_name,
                        input="",
                    ),
                )

            case EventType.TOOL_CALL_DELTA:
                block = self._find_block("tool_call", event.tool_call_id)
                if block is None:
                    logger.warning(
                        "ToolCallBlock %r not found, skipping.",
                        event.tool_call_id,
                    )
                else:
                    assert isinstance(block, ToolCallBlock)
                    block.input += event.delta

            case EventType.TOOL_CALL_END:
                pass

            case EventType.TOOL_RESULT_START:
                self.content.append(
                    ToolResultBlock(
                        id=event.tool_call_id,
                        name=event.tool_call_name,
                        output=[],
                        state=ToolResultState.RUNNING,
                    ),
                )

            case EventType.TOOL_RESULT_TEXT_DELTA:
                block = self._find_block("tool_result", event.tool_call_id)
                if block is None:
                    logger.warning(
                        "ToolResultBlock %r not found, skipping.",
                        event.tool_call_id,
                    )
                else:
                    assert isinstance(block, ToolResultBlock)
                    if isinstance(block.output, str):
                        block.output = [TextBlock(text=block.output)]
                    # Append the text
                    if not block.output or block.output[-1].type != "text":
                        block.output.append(TextBlock(text=event.delta))
                    else:
                        block.output[-1].text += event.delta

            case EventType.TOOL_RESULT_DATA_DELTA:
                block = self._find_block("tool_result", event.tool_call_id)
                if block is None:
                    logger.warning(
                        "ToolResultBlock %r not found, skipping.",
                        event.tool_call_id,
                    )
                else:
                    assert isinstance(block, ToolResultBlock)
                    if isinstance(block.output, str):
                        block.output = [TextBlock(text=block.output)]
                    src = (
                        Base64Source(
                            data=event.data,
                            media_type=event.media_type,
                        )
                        if event.data is not None
                        else URLSource(
                            url=str(event.url),
                            media_type=event.media_type,
                        )
                    )
                    block.output.append(
                        DataBlock(id=event.block_id, source=src),
                    )

            case EventType.TOOL_RESULT_END:
                block = self._find_block("tool_result", event.tool_call_id)
                if block is None:
                    logger.warning(
                        "ToolResultBlock %r not found, skipping.",
                        event.tool_call_id,
                    )
                else:
                    assert isinstance(block, ToolResultBlock)
                    block.state = event.state
                # The paired ToolCallBlock's lifecycle ends with its
                # result — flip it to FINISHED here so the SSE-rebuilt
                # reply_msg matches ``agent.state.context``, which
                # ``_update_tool_call_state`` mutates directly.
                call_block = self._find_block("tool_call", event.tool_call_id)
                if call_block is not None:
                    assert isinstance(call_block, ToolCallBlock)
                    call_block.state = ToolCallState.FINISHED

            case EventType.REQUIRE_USER_CONFIRM:
                for tool_call in event.tool_calls:
                    b = self._find_block("tool_call", tool_call.id)
                    if b is not None:
                        assert isinstance(b, ToolCallBlock)
                        # Update the state
                        b.state = ToolCallState.ASKING
                        # Record the suggestions
                        b.suggested_rules = tool_call.suggested_rules

            case EventType.USER_CONFIRM_RESULT:
                for result in event.confirm_results:
                    b = self._find_block("tool_call", result.tool_call.id)
                    if b is not None:
                        assert isinstance(b, ToolCallBlock)
                        b.state = (
                            ToolCallState.ALLOWED
                            if result.confirmed
                            else ToolCallState.FINISHED
                        )

            case EventType.REQUIRE_EXTERNAL_EXECUTION:
                for tool_call in event.tool_calls:
                    b = self._find_block("tool_call", tool_call.id)
                    if b is not None:
                        assert isinstance(b, ToolCallBlock)
                        b.state = ToolCallState.SUBMITTED

            case EventType.EXTERNAL_EXECUTION_RESULT:
                for result in event.execution_results:
                    self.content.append(result)

        return self


def UserMsg(
    name: str,
    content: str | list[TextBlock | DataBlock],
    metadata: dict | None = None,
    created_at: str | None = None,
    finished_at: str | None = None,
    id: str | None = None,  # pylint: disable=redefined-builtin
) -> Msg:
    """Create a user message with role ``"user"``.

    Args:
        name (`str`):
            The name of the sender.
        content (`str | list[TextBlock | DataBlock]`):
            The message content. A plain string will be automatically wrapped
            in a :class:`TextBlock`. Only :class:`TextBlock` and
            :class:`DataBlock` are allowed for user messages.
        metadata (`dict | None`, optional):
            Arbitrary key-value metadata attached to the message. Defaults to
            an empty dict when not provided.
        created_at (`str | None`, optional):
            ISO-format timestamp for when the message was created. Defaults to
            the current time when not provided.
        finished_at (`str | None`, optional):
            ISO-format timestamp for when the message was finished. Defaults to
            the same value as ``created_at`` when not provided.
        id (`str | None`, optional):
            A unique identifier for the message. A random UUID hex string is
            generated when not provided.

    Returns:
        `Msg`:
            A :class:`Msg` instance with ``role="user"``.
    """
    created_at = created_at or datetime.now().isoformat()
    if finished_at is None:
        finished_at = created_at
    return Msg(
        name=name,
        content=_to_blocks(content),
        role="user",
        metadata=metadata or {},
        created_at=created_at,
        finished_at=finished_at,
        id=id or uuid.uuid4().hex,
    )


def AssistantMsg(
    name: str,
    content: str | list[ContentBlock],
    metadata: dict | None = None,
    created_at: str | None = None,
    finished_at: str | None = None,
    id: str | None = None,  # pylint: disable=redefined-builtin
    usage: Usage | None = None,
) -> Msg:
    """Create an assistant message with role ``"assistant"``.

    Args:
        name (`str`):
            The name of the sender.
        content (`str | list[ContentBlock]`):
            The message content. A plain string will be automatically wrapped
            in a :class:`TextBlock`. Any :class:`ContentBlock` subtype is
            permitted for assistant messages.
        metadata (`dict | None`, optional):
            Arbitrary key-value metadata attached to the message. Defaults to
            an empty dict when not provided.
        created_at (`str | None`, optional):
            ISO-format timestamp for when the message was created. Defaults to
            the current time when not provided.
        finished_at (`str | None`, optional):
            ISO-format timestamp for when the message was finished. Not set by
            default for assistant messages.
        id (`str | None`, optional):
            A unique identifier for the message. A random UUID hex string is
            generated when not provided.
        usage (`Usage | None`, optional):
            The token usage information of the message.

    Returns:
        `Msg`:
            A :class:`Msg` instance with ``role="assistant"``.
    """
    return Msg(
        name=name,
        content=_to_blocks(content),
        role="assistant",
        metadata=metadata or {},
        created_at=created_at or datetime.now().isoformat(),
        finished_at=finished_at,
        id=id or uuid.uuid4().hex,
        usage=usage,
    )


def SystemMsg(
    name: str,
    content: str | list[TextBlock],
    metadata: dict | None = None,
    created_at: str | None = None,
    finished_at: str | None = None,
    id: str | None = None,  # pylint: disable=redefined-builtin
) -> Msg:
    """Create a system message with role ``"system"``.

    Args:
        name (`str`):
            The name of the sender.
        content (`str | list[TextBlock]`):
            The message content. A plain string will be automatically wrapped
            in a :class:`TextBlock`. Only :class:`TextBlock` is allowed for
            system messages.
        metadata (`dict | None`, optional):
            Arbitrary key-value metadata attached to the message. Defaults to
            an empty dict when not provided.
        created_at (`str | None`, optional):
            ISO-format timestamp for when the message was created. Defaults to
            the current time when not provided.
        finished_at (`str | None`, optional):
            ISO-format timestamp for when the message was finished. Defaults to
            the same value as ``created_at`` when not provided.
        id (`str | None`, optional):
            A unique identifier for the message. A random UUID hex string is
            generated when not provided.

    Returns:
        `Msg`:
            A :class:`Msg` instance with ``role="system"``.
    """
    created_at = created_at or datetime.now().isoformat()
    if finished_at is None:
        finished_at = created_at
    return Msg(
        name=name,
        content=_to_blocks(content),
        role="system",
        metadata=metadata or {},
        created_at=created_at,
        finished_at=finished_at,
        id=id or uuid.uuid4().hex,
    )
