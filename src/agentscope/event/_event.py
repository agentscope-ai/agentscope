# -*- coding: utf-8 -*-
"""Event types for agent execution."""
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal, List, TypeAlias

from pydantic import BaseModel, Field, ConfigDict

from ..message import ToolCallBlock, ToolResultBlock, ToolResultState
from ..permission import PermissionRule


class EventType(StrEnum):
    """Event type enumeration."""

    REPLY_START = "REPLY_START"
    REPLY_END = "REPLY_END"

    MODEL_CALL_START = "MODEL_CALL_START"
    MODEL_CALL_END = "MODEL_CALL_END"

    TEXT_BLOCK_START = "TEXT_BLOCK_START"
    TEXT_BLOCK_DELTA = "TEXT_BLOCK_DELTA"
    TEXT_BLOCK_END = "TEXT_BLOCK_END"

    DATA_BLOCK_START = "DATA_BLOCK_START"
    DATA_BLOCK_DELTA = "DATA_BLOCK_DELTA"
    DATA_BLOCK_END = "DATA_BLOCK_END"

    THINKING_BLOCK_START = "THINKING_BLOCK_START"
    THINKING_BLOCK_DELTA = "THINKING_BLOCK_DELTA"
    THINKING_BLOCK_END = "THINKING_BLOCK_END"

    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_DELTA = "TOOL_CALL_DELTA"
    TOOL_CALL_END = "TOOL_CALL_END"

    TOOL_RESULT_START = "TOOL_RESULT_START"
    TOOL_RESULT_TEXT_DELTA = "TOOL_RESULT_TEXT_DELTA"
    TOOL_RESULT_DATA_DELTA = "TOOL_RESULT_DATA_DELTA"
    TOOL_RESULT_END = "TOOL_RESULT_END"

    EXCEED_MAX_ITERS = "EXCEED_MAX_ITERS"

    REQUIRE_USER_CONFIRM = "REQUIRE_USER_CONFIRM"
    REQUIRE_EXTERNAL_EXECUTION = "REQUIRE_EXTERNAL_EXECUTION"

    USER_CONFIRM_RESULT = "USER_CONFIRM_RESULT"
    EXTERNAL_EXECUTION_RESULT = "EXTERNAL_EXECUTION_RESULT"


class EventBase(BaseModel):
    """Base event class."""

    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """Unique event identifier."""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    """ISO 8601 timestamp of when the event was created."""


class ReplyStartEvent(EventBase):
    """Reply start event."""

    type: Literal[EventType.REPLY_START] = EventType.REPLY_START
    """Event type."""
    session_id: str
    """ID of the session this reply belongs to."""
    reply_id: str
    """ID of the reply message produced by this reply."""
    name: str
    """Name of the agent."""
    role: Literal["user", "assistant", "system"] = "assistant"
    """Role of the agent."""


class ReplyEndEvent(EventBase):
    """Reply end event."""

    type: Literal[EventType.REPLY_END] = EventType.REPLY_END
    """Event type."""
    session_id: str
    """ID of the session this reply belongs to."""
    reply_id: str
    """ID of the reply message produced by this reply."""


class ModelCallStartEvent(EventBase):
    """Model call start event."""

    type: Literal[EventType.MODEL_CALL_START] = EventType.MODEL_CALL_START
    """Event type."""
    reply_id: str
    """ID of the reply message this model call belongs to."""
    model_name: str
    """Name of the model being called."""


class ModelCallEndEvent(EventBase):
    """Model call end event."""

    type: Literal[EventType.MODEL_CALL_END] = EventType.MODEL_CALL_END
    """Event type."""
    reply_id: str
    """ID of the reply message this model call belongs to."""
    input_tokens: int
    """Number of input tokens consumed."""
    output_tokens: int
    """Number of output tokens generated."""


class TextBlockStartEvent(EventBase):
    """Text block start event."""

    type: Literal[EventType.TEXT_BLOCK_START] = EventType.TEXT_BLOCK_START
    """Event type."""
    reply_id: str
    """ID of the reply message this block belongs to."""
    block_id: str
    """Unique identifier of the text block."""


class TextBlockDeltaEvent(EventBase):
    """Text block delta event."""

    type: Literal[EventType.TEXT_BLOCK_DELTA] = EventType.TEXT_BLOCK_DELTA
    """Event type."""
    reply_id: str
    """ID of the reply message this block belongs to."""
    block_id: str
    """Unique identifier of the text block."""
    delta: str
    """Incremental text content."""


class TextBlockEndEvent(EventBase):
    """Text block end event."""

    type: Literal[EventType.TEXT_BLOCK_END] = EventType.TEXT_BLOCK_END
    """Event type."""
    reply_id: str
    """ID of the reply message this block belongs to."""
    block_id: str
    """Unique identifier of the text block."""


class DataBlockStartEvent(EventBase):
    """Data block start event."""

    type: Literal[EventType.DATA_BLOCK_START] = EventType.DATA_BLOCK_START
    """Event type."""
    reply_id: str
    """ID of the reply message this block belongs to."""
    block_id: str
    """Unique identifier of the data block."""
    media_type: str
    """MIME type of the data content (e.g. "image/png")."""


class DataBlockDeltaEvent(EventBase):
    """Data block delta event."""

    type: Literal[EventType.DATA_BLOCK_DELTA] = EventType.DATA_BLOCK_DELTA
    """Event type."""
    reply_id: str
    """ID of the reply message this block belongs to."""
    block_id: str
    """Unique identifier of the data block."""
    data: str
    """Incremental base64-encoded data."""
    media_type: str
    """MIME type of the data content."""


class DataBlockEndEvent(EventBase):
    """Data block end event."""

    type: Literal[EventType.DATA_BLOCK_END] = EventType.DATA_BLOCK_END
    """Event type."""
    reply_id: str
    """ID of the reply message this block belongs to."""
    block_id: str
    """Unique identifier of the data block."""


class ThinkingBlockStartEvent(EventBase):
    """Thinking block start event."""

    type: Literal[
        EventType.THINKING_BLOCK_START
    ] = EventType.THINKING_BLOCK_START
    """Event type."""
    reply_id: str
    """ID of the reply message this block belongs to."""
    block_id: str
    """Unique identifier of the thinking block."""


class ThinkingBlockDeltaEvent(EventBase):
    """Thinking block delta event."""

    type: Literal[
        EventType.THINKING_BLOCK_DELTA
    ] = EventType.THINKING_BLOCK_DELTA
    """Event type."""
    reply_id: str
    """ID of the reply message this block belongs to."""
    block_id: str
    """Unique identifier of the thinking block."""
    delta: str
    """Incremental thinking text content."""


class ThinkingBlockEndEvent(EventBase):
    """Thinking block end event."""

    type: Literal[EventType.THINKING_BLOCK_END] = EventType.THINKING_BLOCK_END
    """Event type."""
    reply_id: str
    """ID of the reply message this block belongs to."""
    block_id: str
    """Unique identifier of the thinking block."""


class ToolCallStartEvent(EventBase):
    """Tool call start event."""

    type: Literal[EventType.TOOL_CALL_START] = EventType.TOOL_CALL_START
    """Event type."""
    reply_id: str
    """ID of the reply message this tool call belongs to."""
    tool_call_id: str
    """Unique identifier of the tool call."""
    tool_call_name: str
    """Name of the tool being called."""


class ToolCallDeltaEvent(EventBase):
    """Tool call delta event."""

    type: Literal[EventType.TOOL_CALL_DELTA] = EventType.TOOL_CALL_DELTA
    """Event type."""
    reply_id: str
    """ID of the reply message this tool call belongs to."""
    tool_call_id: str
    """Unique identifier of the tool call."""
    delta: str
    """Incremental tool call arguments (JSON fragment)."""


class ToolCallEndEvent(EventBase):
    """Tool call end event."""

    type: Literal[EventType.TOOL_CALL_END] = EventType.TOOL_CALL_END
    """Event type."""
    reply_id: str
    """ID of the reply message this tool call belongs to."""
    tool_call_id: str
    """Unique identifier of the tool call."""


class ToolResultStartEvent(EventBase):
    """Tool result start event."""

    type: Literal[EventType.TOOL_RESULT_START] = EventType.TOOL_RESULT_START
    """Event type."""
    reply_id: str
    """ID of the reply message this tool result belongs to."""
    tool_call_id: str
    """ID of the corresponding tool call."""
    tool_call_name: str
    """Name of the tool that was called."""


class ToolResultTextDeltaEvent(EventBase):
    """Tool result text delta event."""

    type: Literal[
        EventType.TOOL_RESULT_TEXT_DELTA
    ] = EventType.TOOL_RESULT_TEXT_DELTA
    """Event type."""
    reply_id: str
    """ID of the reply message this tool result belongs to."""
    tool_call_id: str
    """ID of the corresponding tool call."""
    delta: str
    """Incremental text content of the tool result."""


class ToolResultDataDeltaEvent(EventBase):
    """Tool result data delta event."""

    type: Literal[
        EventType.TOOL_RESULT_DATA_DELTA
    ] = EventType.TOOL_RESULT_DATA_DELTA
    """Event type."""
    reply_id: str
    """ID of the reply message this tool result belongs to."""
    tool_call_id: str
    """ID of the corresponding tool call."""
    media_type: str
    """MIME type of the binary content."""
    data: str | None = None
    """Base64-encoded binary data, mutually exclusive with `url`."""
    url: str | None = None
    """URL pointing to the binary content, mutually exclusive with `data`."""


class ToolResultEndEvent(EventBase):
    """Tool result end event."""

    model_config = ConfigDict(use_enum_values=True)

    type: Literal[EventType.TOOL_RESULT_END] = EventType.TOOL_RESULT_END
    """Event type."""
    reply_id: str
    """ID of the reply message this tool result belongs to."""
    tool_call_id: str
    """ID of the corresponding tool call."""
    state: ToolResultState
    """Final execution state of the tool call."""


class ExceedMaxItersEvent(EventBase):
    """Exceeded max iterations event."""

    type: Literal[EventType.EXCEED_MAX_ITERS] = EventType.EXCEED_MAX_ITERS
    """Event type."""
    reply_id: str
    """ID of the reply message associated with this run."""
    name: str
    """Name of the agent."""


class RequireUserConfirmEvent(EventBase):
    """Require user confirm event."""

    type: Literal[
        EventType.REQUIRE_USER_CONFIRM
    ] = EventType.REQUIRE_USER_CONFIRM
    """Event type."""
    reply_id: str
    """ID of the reply message associated with this run."""
    tool_calls: List[ToolCallBlock]
    """Tool calls pending user confirmation."""


class RequireExternalExecutionEvent(EventBase):
    """Require external execution event."""

    type: Literal[
        EventType.REQUIRE_EXTERNAL_EXECUTION
    ] = EventType.REQUIRE_EXTERNAL_EXECUTION
    """Event type."""
    reply_id: str
    """ID of the reply message associated with this run."""
    tool_calls: List[ToolCallBlock]
    """Tool calls to be executed externally."""


class ConfirmResult(BaseModel):
    """Confirm result for a tool call."""

    confirmed: bool
    """Whether the user confirmed the tool call."""
    tool_call: ToolCallBlock
    """The tool call that was confirmed or rejected."""
    rules: list[PermissionRule] | None = None
    """The allowed permission rules for this tool call. This field is only
    applicable when ``confirmed`` is True. In case user modification is
    needed, complete permission rules are used here instead of references to
    the suggested rules in ``RequireUserConfirmEvent``."""


class UserConfirmResultEvent(EventBase):
    """User confirm result event."""

    type: Literal[
        EventType.USER_CONFIRM_RESULT
    ] = EventType.USER_CONFIRM_RESULT
    """Event type."""
    reply_id: str
    """ID of the reply message associated with this run."""
    confirm_results: list[ConfirmResult]
    """Confirmation results for each pending tool call."""


class ExternalExecutionResultEvent(EventBase):
    """External execution result event."""

    type: Literal[
        EventType.EXTERNAL_EXECUTION_RESULT
    ] = EventType.EXTERNAL_EXECUTION_RESULT
    """Event type."""
    reply_id: str
    """ID of the reply message associated with this run."""
    execution_results: List[ToolResultBlock]
    """Results returned by the external executor."""


AgentEvent: TypeAlias = (
    ReplyStartEvent
    | ReplyEndEvent
    | ExceedMaxItersEvent
    | RequireUserConfirmEvent
    | RequireExternalExecutionEvent
    | ModelCallStartEvent
    | ModelCallEndEvent
    | TextBlockStartEvent
    | TextBlockDeltaEvent
    | TextBlockEndEvent
    | DataBlockStartEvent
    | DataBlockDeltaEvent
    | DataBlockEndEvent
    | ThinkingBlockStartEvent
    | ThinkingBlockDeltaEvent
    | ThinkingBlockEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | ToolResultStartEvent
    | ToolResultTextDeltaEvent
    | ToolResultDataDeltaEvent
    | ToolResultEndEvent
    | UserConfirmResultEvent
    | ExternalExecutionResultEvent
)
