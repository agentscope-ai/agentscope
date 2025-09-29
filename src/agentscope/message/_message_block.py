# -*- coding: utf-8 -*-
# pylint: disable=R0901
"""The content blocks of messages"""

from typing import Literal, List
from typing_extensions import TypedDict, Required


class TextBlock(TypedDict, total=False):
    """The text block."""

    type: Required[Literal["text"]]
    """The type of the block"""
    text: str
    """The text content"""


class ThinkingBlock(TypedDict, total=False):
    """The thinking block."""

    type: Required[Literal["thinking"]]
    """The type of the block"""
    thinking: str


class Base64Source(TypedDict, total=False):
    """The base64 source"""

    type: Required[Literal["base64"]]
    """The type of the src, must be `base64`"""

    media_type: Required[str]
    """The media type of the data, e.g. `image/jpeg` or `audio/mpeg`"""

    data: Required[str]
    """The base64 data, in format of RFC 2397"""


class URLSource(TypedDict, total=False):
    """The URL source"""

    type: Required[Literal["url"]]
    """The type of the src, must be `url`"""

    url: Required[str]
    """The URL of the image or audio"""


class ImageBlock(TypedDict, total=False):
    """The image block"""

    type: Required[Literal["image"]]
    """The type of the block"""

    source: Required[Base64Source | URLSource]
    """The src of the image"""


class AudioBlock(TypedDict, total=False):
    """The audio block"""

    type: Required[Literal["audio"]]
    """The type of the block"""

    source: Required[Base64Source | URLSource]
    """The src of the audio"""


class VideoBlock(TypedDict, total=False):
    """The video block"""

    type: Required[Literal["video"]]
    """The type of the block"""

    source: Required[Base64Source | URLSource]
    """The src of the audio"""


class ToolUseBlock(TypedDict, total=False):
    """The tool use block."""

    type: Required[Literal["tool_use"]]
    """The type of the block, must be `tool_use`"""
    id: Required[str]
    """The identity of the tool call"""
    name: Required[str]
    """The name of the tool"""
    input: Required[dict[str, object]]
    """The input of the tool"""


class ToolResultBlock(TypedDict, total=False):
    """The tool result block."""

    type: Required[Literal["tool_result"]]
    """The type of the block"""
    id: Required[str]
    """The identity of the tool call result"""
    output: Required[str | List[TextBlock | ImageBlock | AudioBlock]]
    """The output of the tool function"""
    name: Required[str]
    """The name of the tool function"""


# The content block
ContentBlock = (
    ToolUseBlock
    | ToolResultBlock
    | TextBlock
    | ThinkingBlock
    | ImageBlock
    | AudioBlock
    | VideoBlock
)
