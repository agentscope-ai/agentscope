# -*- coding: utf-8 -*-
"""The content blocks of messages."""
import base64
import binascii
from enum import StrEnum
from typing import Literal, List, TypeAlias, Any
from pydantic import (
    BaseModel,
    Field,
    AnyUrl,
    field_serializer,
    ConfigDict,
    PrivateAttr,
)

from .._utils._common import _generate_id, _generate_timestamp
from ..permission import PermissionRule


class _Base64Accumulator:
    """Incrementally encode concatenated independently encoded chunks."""

    def __init__(self, data: str, validate: bool) -> None:
        decoded = base64.b64decode(data, validate=validate)
        prefix_end = len(decoded) - len(decoded) % 3
        self._prefix = base64.b64encode(decoded[:prefix_end]).decode("ascii")
        self._tail = decoded[prefix_end:]
        self.value = self._materialize()

    def _materialize(self) -> str:
        """Return the complete canonical Base64 value."""
        if not self._tail:
            return self._prefix
        return self._prefix + base64.b64encode(self._tail).decode("ascii")

    def append(self, data: str, validate: bool) -> str:
        """Decode only the new chunk and append its canonical encoding."""
        decoded = self._tail + base64.b64decode(data, validate=validate)
        prefix_end = len(decoded) - len(decoded) % 3
        if prefix_end:
            self._prefix += base64.b64encode(
                decoded[:prefix_end],
            ).decode("ascii")
        self._tail = decoded[prefix_end:]
        self.value = self._materialize()
        return self.value


class TextBlock(BaseModel):
    """The text block."""

    type: Literal["text"] = "text"
    """The type of the text block, which is always 'text'."""
    text: str
    """The text content of the block."""
    id: str = Field(default_factory=_generate_id)
    """The unique identifier of the block."""
    created_at: str = Field(default_factory=_generate_timestamp)
    """The creation time of the block"""
    finished_at: str | None = None
    """The finished time of the block"""


class ThinkingBlock(BaseModel):
    """The thinking block.

    Allows extra provider-specific fields (e.g. Anthropic's ``signature``,
    ``redacted_thinking_data``) via ``extra="allow"`` so that model
    implementations can pass arbitrary metadata without subclassing.

    .. note::
        Anthropic's ``redacted_thinking`` blocks are also stored as
        ``ThinkingBlock`` instances with ``thinking=""`` and the
        encrypted payload in the ``redacted_thinking_data`` extra
        field. Callers filtering by ``type=="thinking"`` (e.g.
        ``get_content_blocks``) will receive both visible and
        redacted blocks.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["thinking"] = "thinking"
    """The type of the thinking block, which is always 'thinking'."""
    thinking: str
    """The thinking content of the block."""
    id: str = Field(default_factory=_generate_id)
    """The unique identifier of the block."""
    created_at: str = Field(default_factory=_generate_timestamp)
    """The creation time of the block"""
    finished_at: str | None = None
    """The finished time of the block"""


class Base64Source(BaseModel):
    """The base64 source."""

    type: Literal["base64"] = "base64"
    """The type of the base64 source, which is always 'base64'."""
    data: str
    """The base64-encoded data."""
    media_type: str
    """The media type of the data, e.g., 'image/png', 'audio/mpeg', etc."""

    _accumulator: _Base64Accumulator | None = PrivateAttr(default=None)


# The accumulator is private model state intentionally managed only here.
# pylint: disable=protected-access
def _append_base64_chunk(
    source: Base64Source,
    data: str,
    *,
    validate: bool = False,
    fallback_to_concat: bool = False,
) -> None:
    """Append an independently encoded Base64 chunk to a source."""
    try:
        if (
            source._accumulator is None
            or source._accumulator.value != source.data
        ):
            source._accumulator = _Base64Accumulator(source.data, validate)
        source.data = source._accumulator.append(data, validate)
    except (binascii.Error, ValueError):
        source._accumulator = None
        if not fallback_to_concat:
            raise
        source.data += data


# pylint: enable=protected-access


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
    id: str = Field(default_factory=_generate_id)
    """The unique identifier of the block."""
    source: Base64Source | URLSource
    """The source of the data, which can be either a base64-encoded string or
    a URL."""
    name: str | None = None
    """The name of the data block, which is optional."""
    created_at: str = Field(default_factory=_generate_timestamp)
    """The creation time of the block"""
    finished_at: str | None = None
    """The finished time of the block"""


class HintBlock(BaseModel):
    """A block used to provide instructions or hints to the LLM during the
    reasoning-acting loop. When passed to the LLM API, the hint block is
    converted into a user message.

    The ``hint`` field can be a plain string (text-only) or a list of
    :class:`TextBlock` / :class:`DataBlock` for multimodal content
    (e.g. a background tool result containing both text and an image).
    """

    type: Literal["hint"] = "hint"
    """The type of the hint block, which is always 'hint'."""
    hint: str | list[TextBlock | DataBlock]
    """The hint content — plain text or a list of content blocks for
    multimodal data."""
    id: str = Field(default_factory=_generate_id)
    """The unique identifier of the block."""
    source: str | None = None
    """The sender or origin of this hint. For team messages this is the
    sender's display name (e.g. ``"alice"``); for system notifications
    it may be ``"system"`` or ``None``."""
    created_at: str = Field(default_factory=_generate_timestamp)
    """The creation time of the block"""
    finished_at: str | None = Field(default_factory=_generate_timestamp)
    """The finished time of the block"""


class ToolCallState(StrEnum):
    """The state of the tool call."""

    PENDING = "pending"
    ASKING = "asking"
    ALLOWED = "allowed"
    SUBMITTED = "submitted"
    FINISHED = "finished"


class ToolCallBlock(BaseModel):
    """The tool call block."""

    model_config = ConfigDict(use_enum_values=True)

    type: Literal["tool_call"] = "tool_call"
    """The type of the tool call block, which is always 'tool_call'."""
    id: str
    """The unique identifier of the tool call block."""
    name: str
    """The name of the tool to be called."""
    input: str
    """The raw JSON string input of the tool, accumulated during streaming."""
    state: ToolCallState = ToolCallState.PENDING
    """The tool call state
    - 'pending': the initial state when the tool call hasn't been processed
     by the permission system
    - 'asking': the tool call is asking and waiting for user confirmation
    - 'allowed': allowed by the permission system/user and waits for execution
    - 'submitted': the tool call has been submitted for external execution
     and is waiting for results event

    Transitions
    -----------
    pending
      ├── permission DENY / input validation failed ──► finished
      ├── permission ASK ──────────────────────────── ► asking
      │       ├── user denied ───────────────────────► finished
      │       └── user approved ─────────────────────► allowed
      └── permission ALLOW ────────────────────────── ► allowed

    allowed
      ├── local tool  ── (execute) ─────────────────► finished
      └── external tool ──────────────────────────── ► submitted

    submitted
      └── ExternalExecutionResultEvent received ─────► finished
    """
    suggested_rules: list[PermissionRule] = Field(default_factory=list)
    """The suggestions for this tool call when asking user, used to maintain
    the suggestions across requests."""
    created_at: str = Field(default_factory=_generate_timestamp)
    """The creation time of the block"""
    finished_at: str | None = None
    """The finished time of the block"""


class ToolResultState(StrEnum):
    """The tool result state."""

    SUCCESS = "success"
    ERROR = "error"
    INTERRUPTED = "interrupted"
    DENIED = "denied"
    RUNNING = "running"


class ToolResultBlock(BaseModel):
    """The tool result block."""

    model_config = ConfigDict(use_enum_values=True)

    type: Literal["tool_result"] = "tool_result"
    """The type of the tool result block, which is always 'tool_result'."""
    id: str
    """The unique identifier of the tool result block."""
    name: str
    """The name of the tool."""
    output: str | List[TextBlock | DataBlock]
    """The output of the tool, which can be a raw string of a list of
    text and multimodal blocks."""
    state: ToolResultState = ToolResultState.RUNNING
    """The execution state of the tool."""
    metadata: dict[str, Any] = Field(default_factory=dict)
    """The metadata of the tool result block."""
    created_at: str = Field(default_factory=_generate_timestamp)
    """The creation time of the block"""
    finished_at: str | None = None
    """The finished time of the block"""


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
