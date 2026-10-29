# -*- coding: utf-8 -*-
"""The chat endpoint schema."""

from typing import Self

from pydantic import BaseModel, Field, model_validator

from ....message import (
    ContentBlock,
    DataBlock,
    HintBlock,
    Msg,
    ToolResultBlock,
    URLSource,
)
from ....event import UserConfirmResultEvent, ExternalExecutionResultEvent


def _reject_file_url_sources(blocks: list[ContentBlock]) -> None:
    """Reject local-file data sources supplied through the chat API."""
    for block in blocks:
        if isinstance(block, DataBlock):
            if (
                isinstance(block.source, URLSource)
                and block.source.url.scheme == "file"
            ):
                raise ValueError(
                    "file:// URL sources are not accepted by the chat API. "
                    "Upload content as base64 instead.",
                )
        elif isinstance(block, HintBlock) and isinstance(block.hint, list):
            _reject_file_url_sources(block.hint)
        elif isinstance(block, ToolResultBlock) and isinstance(
            block.output,
            list,
        ):
            _reject_file_url_sources(block.output)


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
    def reject_file_url_sources(self) -> Self:
        """Keep request data sources from reading files on the service host."""
        if isinstance(self.input, Msg):
            _reject_file_url_sources(self.input.content)
        elif isinstance(self.input, list):
            for msg in self.input:
                _reject_file_url_sources(msg.content)
        elif isinstance(self.input, ExternalExecutionResultEvent):
            for result in self.input.execution_results:
                if isinstance(result.output, list):
                    _reject_file_url_sources(result.output)
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
