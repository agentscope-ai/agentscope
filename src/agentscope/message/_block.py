# -*- coding: utf-8 -*-
"""The content blocks of messages."""
import uuid
from typing import Literal, List, TypeAlias
from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    """The text block."""

    type: Literal["text"] = "text"
    text: str
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)


class ThinkingBlock(BaseModel):
    """The thinking block."""

    type: Literal["thinking"] = "thinking"
    thinking: str
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)


class HintBlock(BaseModel):
    """The hint block."""

    type: Literal["hint"] = "hint"
    hint: str
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)


class Base64Source(BaseModel):
    """The base64 source."""

    type: Literal["base64"] = "base64"
    data: str
    media_type: str


class URLSource(BaseModel):
    """The URL source."""

    type: Literal["url"] = "url"
    url: str
    media_type: str


class DataBlock(BaseModel):
    """The data block for binary content (images, audio, video, etc.)."""

    type: Literal["data"] = "data"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    source: Base64Source | URLSource
    name: str | None = None


class ToolCallBlock(BaseModel):
    """The tool call block."""

    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    input: str
    """The raw JSON string input of the tool, accumulated during streaming."""
    await_user_confirmation: bool = False


class ToolResultBlock(BaseModel):
    """The tool result block."""

    type: Literal["tool_result"] = "tool_result"
    id: str
    name: str
    output: str | List[TextBlock | DataBlock]
    state: Literal["success", "error", "interrupted", "running"]


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
