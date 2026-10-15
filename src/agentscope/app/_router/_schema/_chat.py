# -*- coding: utf-8 -*-
"""The chat endpoint schema."""

from typing import Self

from pydantic import BaseModel, Field, model_validator

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

    @model_validator(mode="after")
    def _validate_message_roles(self) -> Self:
        """Reject server-authored roles at the public chat boundary."""
        if isinstance(self.input, Msg):
            messages = [self.input]
        elif isinstance(self.input, list):
            messages = self.input
        else:
            return self

        for message in messages:
            if message.role != "user":
                raise ValueError(
                    "The chat API only accepts client messages with "
                    "role='user'.",
                )
        return self


class ChatTriggerResponse(BaseModel):
    """Response body for the fire-and-forget chat trigger.

    Confirms that the chat run was scheduled. Events produced by the
    run arrive separately via the session's SSE stream endpoint.
    """

    status: str = Field(
        default="started",
        description='Always ``"started"`` when the trigger succeeded.',
    )
    session_id: str = Field(
        description="Echo of the session id the run was started for.",
    )
