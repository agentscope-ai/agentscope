# -*- coding: utf-8 -*-
"""Validate the final inputs of a local notification tool before execution."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentscope.tool import (
    FunctionTool,
    ToolBase,
    ToolValidationMiddleware,
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


def validate_notification(
    context: ExecutionContext,
    tool: ToolBase,
    input_kwargs: dict[str, Any],
) -> str | None:
    """Return a rejection reason, or permit this host-authorized action."""
    if (
        context.agent_id != "support-agent"
        or context.workflow_id != "ticket-42"
    ):
        return "The host context is not authorized."
    if not datetime.now(timezone.utc) < context.valid_until:
        return "The host context has expired."
    if tool.name != "send_notification":
        return "The action is not authorized."
    if input_kwargs["recipient"] != "support@example.com":
        return "The recipient is not authorized."
    return None


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
        middlewares=[
            ToolValidationMiddleware(partial(validate_notification, context)),
        ],
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
