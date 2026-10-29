# -*- coding: utf-8 -*-
"""The chat endpoint schema."""

from pydantic import BaseModel, Field, field_validator, model_validator

from ..._client_external_tool import (
    ClientBuiltinToolName,
    ClientExternalToolDefinition,
)
from ....message import Msg, ToolResultState
from ....tool import AskUser, AskUserMetadata
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

    client_builtin_tools: list[ClientBuiltinToolName] = Field(
        default_factory=list,
        max_length=1,
        description="Native external tools supported by this client run.",
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

    @model_validator(mode="after")
    def validate_native_answers(self) -> "ChatRequest":
        """Validate resumed native results even if the tool was disabled.

        Disabling a tool governs future calls, not the contract of a call
        already waiting for its result.
        """
        if isinstance(self.input, ExternalExecutionResultEvent):
            for result in self.input.execution_results:
                if (
                    result.name == AskUser.name
                    and result.state == ToolResultState.SUCCESS
                ):
                    AskUserMetadata.model_validate(result.metadata)
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
