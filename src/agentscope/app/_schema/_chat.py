# -*- coding: utf-8 -*-
""""""

from pydantic import BaseModel, Field

from ...message import Msg
from ...event import UserConfirmResultEvent, ExternalExecutionResultEvent


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
    )= Field(
        description="The input message(s), or agent event, or None."
    )
