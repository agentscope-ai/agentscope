# -*- coding: utf-8 -*-
"""Validate the final inputs of a local notification tool before execution."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Callable

import jsonschema
from pydantic import BaseModel, ConfigDict, Field

from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import (
    FunctionTool,
    ToolBase,
    ToolChunk,
    ToolMiddlewareBase,
)


@dataclass(frozen=True)
class ExecutionContext:
    """Trusted host context for one workflow, separate from model input."""

    agent_id: str
    workflow_id: str
    valid_until: datetime


class NotificationParams(BaseModel):
    """The model may propose a recipient and message, not its own authority."""

    model_config = ConfigDict(extra="forbid")

    recipient: str
    message: str = Field(min_length=1, max_length=200)


class NotificationValidationMiddleware(ToolMiddlewareBase):
    """Apply this example's policy as the LAST middleware of the tool."""

    def __init__(self, context: ExecutionContext) -> None:
        self.context = context

    async def on_tool_call(
        self,
        tool: ToolBase,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[ToolChunk, None]],
    ) -> AsyncGenerator[ToolChunk, None]:
        """Reject before invoking the function, or pass through its output."""
        reason = None
        try:
            # Agent-side schema checks run before input-rewriting middleware.
            jsonschema.validate(input_kwargs, tool.input_schema)
        except jsonschema.ValidationError:
            reason = "The final tool arguments do not match the schema."

        if reason is None:
            if (
                self.context.agent_id != "support-agent"
                or self.context.workflow_id != "ticket-42"
            ):
                reason = "The host context is not authorized."
            elif not datetime.now(timezone.utc) < self.context.valid_until:
                reason = "The host context has expired."
            elif tool.name != "send_notification":
                reason = "The action is not authorized."
            elif input_kwargs["recipient"] != "support@example.com":
                reason = "The recipient is not authorized."

        if reason is not None:
            yield ToolChunk(
                content=[TextBlock(text=reason)],
                state=ToolResultState.DENIED,
            )
            return

        async for chunk in next_handler(**input_kwargs):
            yield chunk


async def main() -> None:
    """Run proposed actions offline; only an allowed call changes ``sent``."""
    sent: list[tuple[str, str]] = []

    def send_notification(recipient: str, message: str) -> str:
        """Record a notification locally without contacting a mail server."""
        sent.append((recipient, message))
        return "Notification recorded."

    context = ExecutionContext(
        agent_id="support-agent",
        workflow_id="ticket-42",
        valid_until=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    tool = FunctionTool(
        send_notification,
        input_schema=NotificationParams,
        middlewares=[NotificationValidationMiddleware(context)],
    )
    for label, payload in [
        (
            "allowed",
            {
                "recipient": "support@example.com",
                "message": "Ticket 42 is ready for review.",
            },
        ),
        (
            "blocked recipient",
            {"recipient": "outside@example.com", "message": "Ticket 42"},
        ),
        (
            "invalid arguments",
            {"recipient": "support@example.com", "message": 42},
        ),
    ]:
        async for chunk in await tool(**payload):
            print(f"{label}: {chunk.content[0].text}")
    print(f"Notifications recorded: {len(sent)}")


if __name__ == "__main__":
    asyncio.run(main())
