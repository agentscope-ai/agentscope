# -*- coding: utf-8 -*-
"""The chat endpoint schema."""

from typing import Literal

from pydantic import BaseModel, Field

from ....message import Msg
from ....event import UserConfirmResultEvent, ExternalExecutionResultEvent


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
        | list[Msg]
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | None
    ) = Field(
        description="The input message(s), or agent event, or None.",
    )


class ChatTriggerResponse(BaseModel):
    """Response body for the fire-and-forget chat trigger.

    Confirms that the chat run was scheduled. Events produced by the
    run arrive separately via the session's SSE stream endpoint.
    """

    status: Literal["started", "command_completed"] = Field(
        default="started",
        description=(
            '``"started"`` for chat runs or ``"command_completed"`` '
            "for synchronous commands."
        ),
    )
    session_id: str = Field(
        description="Echo of the session id the run was started for.",
    )
    command: str | None = Field(
        default=None,
        description="Completed slash command, when status identifies one.",
    )
