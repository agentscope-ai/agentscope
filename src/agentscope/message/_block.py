# -*- coding: utf-8 -*-
"""The content blocks of messages."""
import uuid
from typing import Literal, List, TypeAlias
from pydantic import BaseModel, Field, AnyUrl, field_serializer


class TextBlock(BaseModel):
    """The text block."""

    type: Literal["text"] = "text"
    """The type of the text block, which is always 'text'."""
    text: str
    """The text content of the block."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The unique identifier of the block."""


class ThinkingBlock(BaseModel):
    """The thinking block."""

    type: Literal["thinking"] = "thinking"
    """The type of the thinking block, which is always 'thinking'."""
    thinking: str
    """The thinking content of the block."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The unique identifier of the block."""


class HintBlock(BaseModel):
    """A block used to provide instructions or hints to the LLM during the
    reasoning-acting loop. When passed to the LLM API, the hint block is
    converted into a user message.
    """

    type: Literal["hint"] = "hint"
    """The type of the hint block, which is always 'hint'."""
    hint: str
    """The hint content of the block."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The unique identifier of the block."""


class Base64Source(BaseModel):
    """The base64 source."""

    type: Literal["base64"] = "base64"
    """The type of the base64 source, which is always 'base64'."""
    data: str
    """The base64-encoded data."""
    media_type: str
    """The media type of the data, e.g., 'image/png', 'audio/mpeg', etc."""


class URLSource(BaseModel):
    """The URL source."""

    type: Literal["url"] = "url"
    """The type of the URL source, which is always 'url'."""
    url: AnyUrl
    """A valid URI string conforming to RFC 3986."""
    media_type: str
    """The media type of the data, e.g., 'image/png', 'audio/mpeg', etc."""

    @field_serializer("url")
    def serialize_url(self, url: AnyUrl) -> str:
        """Serialize the URL to a string."""
        return str(url)


class DataBlock(BaseModel):
    """The data block for binary content (images, audio, video, etc.)."""

    type: Literal["data"] = "data"
    """The type of the data block, which is always 'data'."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The unique identifier of the block."""
    source: Base64Source | URLSource
    """The source of the data, which can be either a base64-encoded string or
    a URL."""
    name: str | None = None
    """The name of the data block, which is optional."""


class ToolCallBlock(BaseModel):
    """The tool call block."""

    type: Literal["tool_call"] = "tool_call"
    """The type of the tool call block, which is always 'tool_call'."""
    id: str
    """The unique identifier of the tool call block."""
    name: str
    """The name of the tool to be called."""
    input: str
    """The raw JSON string input of the tool, accumulated during streaming."""
    await_user_confirmation: bool = False
    """Whether to await user confirmation before executing the tool."""


class ToolResultBlock(BaseModel):
    """The tool result block."""

    type: Literal["tool_result"] = "tool_result"
    """The type of the tool result block, which is always 'tool_result'."""
    id: str
    """The unique identifier of the tool result block."""
    name: str
    """The name of the tool."""
    output: str | List[TextBlock | DataBlock]
    """The output of the tool, which can be a raw string of a list of
    text and multimodal blocks."""
    state: Literal["success", "error", "interrupted", "running"]
    """The execution state of the tool."""


ContentBlock: TypeAlias = (
    TextBlock
    | ThinkingBlock
    | HintBlock
    | ToolCallBlock
    | ToolResultBlock
    | DataBlock
)

ContentBlockTypes: TypeAlias = Literal[
    "text",
    "thinking",
    "hint",
    "tool_call",
    "tool_result",
    "data",
]
