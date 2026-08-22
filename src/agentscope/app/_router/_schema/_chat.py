# -*- coding: utf-8 -*-
"""The chat endpoint schema."""

from pydantic import BaseModel, Field, field_validator

from ..._client_external_tool import ClientExternalToolDefinition
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

    client_external_tools: list[ClientExternalToolDefinition] = Field(
        default_factory=list,
        max_length=16,
        description=(
            "External tools the active client can execute for this run."
        ),
    )

    @field_validator("client_external_tools")
    @classmethod
    def validate_unique_client_external_tools(
        cls,
        value: list[ClientExternalToolDefinition],
    ) -> list[ClientExternalToolDefinition]:
        """Reject ambiguous duplicate client tool names."""
        names = [tool.name for tool in value]
        if len(names) != len(set(names)):
            raise ValueError("Client external tool names must be unique.")
        return value


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
