# -*- coding: utf-8 -*-
"""The message class in agentscope."""
from datetime import datetime
from typing import Literal, List, overload, Sequence, Self

import shortuuid
from pydantic import BaseModel, Field, model_validator

from ._block import (
    TextBlock,
    ToolCallBlock,
    DataBlock,
    ContentBlock,
    ToolResultBlock,
    ContentBlockTypes,
    HintBlock,
    ThinkingBlock,
)


def _assert_user_content_blocks(content: str | Sequence[ContentBlock]) -> None:
    """Assert that the content blocks in user message are valid."""
    if isinstance(content, str):
        return

    for block in content:
        if block.type not in ["text", "data"]:
            raise ValueError(
                "User message can only contain text blocks or data blocks.",
            )


def _assert_system_content_blocks(
    content: str | Sequence[ContentBlock],
) -> None:
    """Assert that the content blocks in system message are valid."""
    if isinstance(content, str):
        return

    for block in content:
        if block.type not in ["text"]:
            raise ValueError("System message can only contain text blocks.")


class Msg(BaseModel):
    """The message class in AgentScope, responsible for information storage
    and transmission among different agents."""

    name: str
    """The name of the sender."""
    content: str | list[ContentBlock]
    """The message content, a string or a list of content blocks."""
    role: Literal["user", "assistant", "system"]
    """The role of the sender."""
    id: str = Field(default_factory=shortuuid.uuid)
    """The message identifier."""
    metadata: dict = Field(default_factory=dict)
    """The metadata of the message"""
    created_at: str = Field(default_factory=datetime.now().isoformat)
    """The creation time of the message"""
    ended_at: str | None = Field(default=None)
    """The end time of the message, used for calculating the response time of
    the assistant message."""

    @model_validator(mode="after")
    def validate_msg(self) -> Self:
        """Validate the message fields."""
        match self.role:
            case "user":
                _assert_user_content_blocks(self.content)
            case "system":
                _assert_system_content_blocks(self.content)
            case "assistant":
                pass

        if self.ended_at is not None and datetime.fromisoformat(
            self.ended_at,
        ) < datetime.fromisoformat(self.created_at):
            raise ValueError(
                f"ended_at ({self.ended_at}) must be >= created_at "
                f"({self.created_at}).",
            )

        return self

    def has_content_blocks(
        self,
        block_type: ContentBlockTypes | None = None,
    ) -> bool:
        """Check if the message has content blocks of the given type.

        Args:
            block_type (ContentBlockTypes | None, defaults to None):
                The type of the block to be checked. If `None`, it will
                check if there are any content blocks.
        """
        return len(self.get_content_blocks(block_type)) > 0

    def get_text_content(self, separator: str = "\n") -> str | None:
        """Get the pure text blocks from the message content.

        Args:
            separator (`str`, defaults to `\n`):
                The separator to use when concatenating multiple text blocks.
                Defaults to newline character.

        Returns:
            `str | None`:
                The concatenated text content, or `None` if there is no text
                content.
        """
        if isinstance(self.content, str):
            return self.content

        gathered_text = []
        for block in self.content:
            if block.type == "text":
                gathered_text.append(block.text)

        if gathered_text:
            return separator.join(gathered_text)

        return None

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
        """Get the content in block format. If the content is a string,
        it will be converted to a text block.

        Args:
            block_type (`ContentBlockTypes | List[ContentBlockTypes] | None`, \
            optional):
                The type of the block to be extracted. If `None`, all blocks
                will be returned.

        Returns:
            `List[ContentBlock]`:
                The content blocks.
        """
        blocks = []
        if isinstance(self.content, str):
            blocks.append(
                TextBlock(text=self.content),
            )
        else:
            blocks = self.content or []

        if isinstance(block_type, str):
            blocks = [_ for _ in blocks if _.type == block_type]

        elif isinstance(block_type, list):
            blocks = [_ for _ in blocks if _.type in block_type]

        return blocks


def UserMsg(
    name: str,
    content: str | list[TextBlock | DataBlock],
    metadata: dict | None = None,
    created_at: str | None = None,
) -> Msg:
    """Create a user message with role "user".

    Args:
        name (`str`):
            The name of the message sender.
        content (`str | list[TextBlock | DataBlock]`):
            The content of the message. It can be a string or a list of
            TextBlock or DataBlock.
        metadata (`dict | None`, optional):
            The metadata of the message. Defaults to `None`.
        created_at (`str | None`, optional):
            The creation time of the message in ISO format. Defaults to `None`.

    Returns:
        `Msg`:
            The created user message.
    """

    return Msg(
        name=name,
        content=content,
        role="user",
        metadata=metadata or {},
        created_at=created_at or datetime.now().isoformat(),
    )


def AssistantMsg(
    name: str,
    content: str | list[ContentBlock],
    metadata: dict | None = None,
    created_at: str | None = None,
) -> Msg:
    """Create an assistant message with role "assistant".

    Args:
        name (`str`):
            The name of the message sender.
        content (`str | list[ContentBlock]`):
            The content of the message. It can be a string or a list of
            ContentBlock.
        metadata (`dict | None`, optional):
            The metadata of the message. Defaults to `None`.
        created_at (`str | None`, optional):
            The creation time of the message in ISO format. Defaults to `None`.

    Returns:
        `Msg`:
            The created assistant message.
    """
    return Msg(
        name=name,
        content=content,
        role="assistant",
        metadata=metadata or {},
        created_at=created_at or datetime.now().isoformat(),
    )


def SystemMsg(
    name: str,
    content: str | list[TextBlock],
    metadata: dict | None = None,
    created_at: str | None = None,
) -> Msg:
    """Create a system message with role "system".

    Args:
        name (`str`):
            The name of the message sender.
        content (`str | list[TextBlock]`):
            The content of the message. It can be a string or a list of
            TextBlock.
        metadata (`dict | None`, optional):
            The metadata of the message. Defaults to `None`.
        created_at (`str | None`, optional):
            The creation time of the message in ISO format. Defaults to `None`.

    Returns:
        `Msg`:
            The created system message.
    """

    return Msg(
        name=name,
        content=content,
        role="system",
        metadata=metadata or {},
        created_at=created_at or datetime.now().isoformat(),
    )
