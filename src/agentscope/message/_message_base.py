# -*- coding: utf-8 -*-
"""The message class in agentscope."""
from datetime import datetime
from typing import Literal, List, overload, Sequence

import shortuuid

from . import ToolResultBlock
from ._message_block import (
    TextBlock,
    ToolUseBlock,
    ImageBlock,
    AudioBlock,
    ContentBlock,
    VideoBlock,
    ThinkingBlock,
)
from ..types import JSONSerializableObject


class Msg:
    """The message class in agentscope."""

    def __init__(
        self,
        name: str,
        content: str | Sequence[ContentBlock],
        role: Literal["user", "assistant", "system"],
        metadata: dict[str, JSONSerializableObject] | None = None,
        timestamp: str | None = None,
        invocation_id: str | None = None,
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
            invocation_id (`str | None`, optional):
                The related API invocation id, if any. This is useful for
                tracking the message in the context of an API call.
        """

        self.name = name

        assert isinstance(
            content,
            (list, str),
        ), "The content must be a string or a list of content blocks."

        self.content = content

        assert role in ["user", "assistant", "system"]
        self.role = role

        self.metadata = metadata

        self.id = shortuuid.uuid()
        self.timestamp = (
            timestamp
            or datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S.%f",
            )[:-3]
        )
        self.invocation_id = invocation_id

    def to_dict(self) -> dict:
        """Convert the message into JSON dict data."""
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "content": self.content,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
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
            invocation_id=json_data.get("invocation_id", None),
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

    def get_text_content(self) -> str | None:
        """Get the pure text blocks from the message content."""
        if isinstance(self.content, str):
            return self.content

        gathered_text = None
        for block in self.content:
            if block.get("type") == "text":
                if gathered_text is None:
                    gathered_text = str(block.get("text"))
                else:
                    gathered_text += block.get("text")
        return gathered_text

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["text"],
    ) -> List[TextBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["tool_use"],
    ) -> List[ToolUseBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["tool_result"],
    ) -> List[ToolResultBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["image"],
    ) -> List[ImageBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["audio"],
    ) -> List[AudioBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: Literal["video"],
    ) -> List[VideoBlock]:
        ...

    @overload
    def get_content_blocks(
        self,
        block_type: None = None,
    ) -> List[ContentBlock]:
        ...

    def get_content_blocks(
        self,
        block_type: Literal[
            "text",
            "thinking",
            "tool_use",
            "tool_result",
            "image",
            "audio",
            "video",
        ]
        | None = None,
    ) -> (
        List[ContentBlock]
        | List[TextBlock]
        | List[ThinkingBlock]
        | List[ToolUseBlock]
        | List[ToolResultBlock]
        | List[ImageBlock]
        | List[AudioBlock]
        | List[VideoBlock]
    ):
        """Get the content in block format. If the content is a string,
        it will be converted to a text block.

        Args:
            block_type (`Literal["text", "thinking", "tool_use", \
            "tool_result", "image", "audio", "video"] | None`, optional):
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

        if block_type is not None:
            blocks = [_ for _ in blocks if _["type"] == block_type]

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
