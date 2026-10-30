# -*- coding: utf-8 -*-
"""The chat endpoint schema."""

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, Field

from ....message import Msg
from ....event import UserConfirmResultEvent, ExternalExecutionResultEvent

NonEmptyMsgList: TypeAlias = Annotated[list[Msg], Field(min_length=1)]


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""

    agent_id: str = Field(
        description="Agent ID for the chat endpoint.",
    )

    session_id: str = Field(
        description="The session to send the message to.",
    )

    input: (
        Msg
        | NonEmptyMsgList
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | None
    ) = Field(
        description="The input message(s), or agent event, or None.",
    )


class ChatTriggerResponse(BaseModel):
    """Response body for an accepted chat trigger.

    An ordinary user turn is accepted into the FIFO with ``queued``;
    continuation and control triggers use ``started``. Resulting events
    arrive separately via the session's SSE stream endpoint.
    """

    status: Literal["started", "queued"] = Field(
        default="started",
        description=(
            '``"queued"`` for an accepted ordinary user turn; '
            '``"started"`` for continuation/control triggers.'
        ),
    )
    session_id: str = Field(
        description="Session whose queue or run accepted the trigger.",
    )
    queue_item_id: str | None = Field(
        default=None,
        description="Stable queue item id for an ordinary user turn.",
    )


class ChatQueueItem(BaseModel):
    """One ordinary user turn that has not started execution."""

    id: str = Field(
        description="Stable id used to edit, delete, or reorder this item.",
    )
    created_at: str = Field(
        description="UTC ISO-8601 timestamp recorded when the item queued.",
    )
    input: Msg | NonEmptyMsgList = Field(
        description="One message or non-empty ordered message list.",
    )


class ChatQueueResponse(BaseModel):
    """Current editable FIFO snapshot for a session."""

    items: list[ChatQueueItem] = Field(
        description="Editable pending turns in FIFO execution order.",
    )


class UpdateChatQueueItemRequest(BaseModel):
    """Replace the input carried by one pending queue item."""

    agent_id: str = Field(
        description="Agent that owns the target session.",
    )
    session_id: str = Field(
        description="Session whose pending item will be updated.",
    )
    input: Msg | NonEmptyMsgList = Field(
        description="Replacement message or non-empty message list.",
    )


class ReorderChatQueueRequest(BaseModel):
    """Set the complete pending queue order."""

    agent_id: str = Field(
        description="Agent that owns the target session.",
    )
    session_id: str = Field(
        description="Session whose pending FIFO will be reordered.",
    )
    item_ids: list[str] = Field(
        description="Exact desired permutation of all pending item ids.",
    )
