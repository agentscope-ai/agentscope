# -*- coding: utf-8 -*-
"""SendMessage — send text to another Slack conversation/user."""
from pydantic import Field

from .....tool import ParamsBase, ToolChunk
from ._base import _SlackToolBase, _ack


class _SendMessageParams(ParamsBase):
    chat_id: str = Field(
        description="Target id, taken verbatim from a ListChats / "
        "ListChatMembers result. A user id opens a direct message.",
    )
    text: str = Field(description="The message text to send.")


class SendMessage(_SlackToolBase):
    """Send text to another Slack conversation/user."""

    name: str = "SendMessage"
    description: str = """Send a text message to a Slack channel or person \
OTHER than the current conversation.

## When to Use
- The user asks you to notify or relay something to a *different* channel \
or person (e.g. "tell #finance ...", "let Alice know ...").

## When NOT to Use
- To answer the person you are talking with now — that reply is sent \
automatically. Never use this tool for the current conversation.

## How to Use
Obtain ``chat_id`` first: a channel's via ``ListChats``, a person's via \
``ListChatMembers``. Sending requires the user's confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendMessageParams.model_json_schema()

    async def __call__(self, chat_id: str, text: str) -> ToolChunk:
        """Send ``text`` to ``chat_id``.

        Args:
            chat_id (`str`): Target id from a discovery result.
            text (`str`): The message text to send.
        """
        data = await self._channel.send_message_to(chat_id, text)
        return _ack(data, f"message to {chat_id}")
