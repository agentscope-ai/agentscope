# -*- coding: utf-8 -*-
"""The message class in agentscope."""
from datetime import datetime
from enum import Enum
from typing import Literal, List, overload, Sequence

import shortuuid

from ._message_block import (
    TextBlock,
    ToolUseBlock,
    ImageBlock,
    AudioBlock,
    ContentBlock,
    VideoBlock,
    ToolResultBlock,
    ContentBlockTypes,
)
from ..types import JSONSerializableObject


class GenerateReason(str, Enum):
    """The reason for the agent generating the reply message."""

    NORMAL_REPLY = "normal_reply"
    """The message is a normal reply from the agent, which contains the
    response to the outside user or agent."""

    LLM_REASONING = "llm_reasoning"
    """The message contains the agent's internal reasoning process, which
    is generated to decide the next action or response."""

    MAX_ITERATIONS = "max_iterations"
    """The agent has reached the maximum number of iterations, and generates
    this summary message to conclude its work."""

    INTERRUPTED = "interrupted"
    """The agent's work has been interrupted by an external signal, and this
    message is generated within the interruption handling process."""

    AWAITING_TOOL_EXECUTION = "awaiting_tool_execution"
    """The message contains tool use block(s) for external tools, which
    require execution outside the agent's own capabilities. Awaiting the
    next input with tool execution results."""

    TOOL_EXECUTION_RESULT = "tool_execution_result"
    """The message contains the results of tool execution for the previously
    requested tool use block(s)."""

    AWAITING_USER_CONFIRMATION = "awaiting_user_confirmation"
    """The message contains tool use block(s) that require user confirmation
    before execution. Awaiting the next input with user confirmation
    results."""

    USER_CONFIRMATION_APPROVED = "user_confirmation_approved"
    """The user approved the tool use block(s) in the content. The tool(s)
    should be executed as requested."""

    USER_CONFIRMATION_REJECTED = "user_confirmation_rejected"
    """The user rejected the tool use block(s). The content may contain
    the rejection reason or explanation."""

    USER_CONFIRMATION_MODIFIED = "user_confirmation_modified"
    """The user modified and approved the tool use block(s). The content
    contains the modified tool use blocks that should be executed."""


class Msg:
    """The message class in agentscope."""

    def __init__(
        self,
        name: str,
        content: str | Sequence[ContentBlock],
        role: Literal["user", "assistant", "system"],
        metadata: dict[str, JSONSerializableObject] | None = None,
        timestamp: str | None = None,
        generate_reason: GenerateReason | None = None,
    ) -> None:
        """Initialize the Msg object.

        Args:
            name (`str`):
                The name of the message sender.
            content (`str | list[ContentBlock]`):
                The content of the message.
            role (`Literal["user", "assistant", "system"]`):
                The role of the message sender.
            metadata (`dict[str, JSONSerializableObject] | None`, optional):
                The metadata of the message, e.g. structured output.
            timestamp (`str | None`, optional):
                The created timestamp of the message. If not given, the
                timestamp will be set automatically.
            generate_reason (`GenerateReason | None`, optional):
                The reason for generating this message.
        """

        self.name = name

        assert isinstance(
            content,
            (list, str),
        ), "The content must be a string or a list of content blocks."

        self.content = content

        assert role in ["user", "assistant", "system"]
        self.role = role

        self.metadata = metadata or {}

        self.id = shortuuid.uuid()
        self.timestamp = (
            timestamp
            or datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S.%f",
            )[:-3]
        )
        self.generate_reason = generate_reason

    def to_dict(self) -> dict:
        """Convert the message into JSON dict data."""
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "content": self.content,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "generate_reason": self.generate_reason,
        }

    @classmethod
    def from_dict(cls, json_data: dict) -> "Msg":
        """Load a message object from the given JSON data."""
        new_obj = cls(
            name=json_data["name"],
            content=json_data["content"],
            role=json_data["role"],
            metadata=json_data.get("metadata", None),
            timestamp=json_data.get("timestamp", None),
            generate_reason=json_data.get("generate_reason", None),
        )

        new_obj.id = json_data.get("id", new_obj.id)
        return new_obj

    def has_content_blocks(
        self,
        block_type: Literal[
            "text",
            "tool_use",
            "tool_result",
            "image",
            "audio",
            "video",
        ]
        | None = None,
    ) -> bool:
        """Check if the message has content blocks of the given type.

        Args:
            block_type (Literal["text", "tool_use", "tool_result", "image", \
            "audio", "video"] | None, defaults to None):
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
            if block.get("type") == "text":
                gathered_text.append(block["text"])

        if gathered_text:
            return separator.join(gathered_text)

        return None

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["text"],
    ) -> Sequence[TextBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["tool_use"],
    ) -> Sequence[ToolUseBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["tool_result"],
    ) -> Sequence[ToolResultBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["image"],
    ) -> Sequence[ImageBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["audio"],
    ) -> Sequence[AudioBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["video"],
    ) -> Sequence[VideoBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: None = None,
    ) -> Sequence[ContentBlock]:
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
                TextBlock(type="text", text=self.content),
            )
        else:
            blocks = self.content or []

        if isinstance(block_type, str):
            blocks = [_ for _ in blocks if _["type"] == block_type]

        elif isinstance(block_type, list):
            blocks = [_ for _ in blocks if _["type"] in block_type]

        return blocks

    def __repr__(self) -> str:
        """Get the string representation of the message."""
        return (
            f"Msg(id='{self.id}', "
            f"name='{self.name}', "
            f"content={repr(self.content)}, "
            f"role='{self.role}', "
            f"metadata={repr(self.metadata)}, "
            f"timestamp='{self.timestamp}', "
            f"invocation_id='{self.invocation_id}')"
        )
