# -*- coding: utf-8 -*-
"""SendMessage — send text to another Discord channel/user."""
from pydantic import Field

from .....tool import ParamsBase, ToolChunk
from ._base import _DiscordToolBase, _sent


class _SendMessageParams(ParamsBase):
    target_id: str = Field(
        description="Target id, taken verbatim from a ListChats / "
        "ListChatMembers result.",
    )
    target_type: str = Field(
        description="Must match the id: 'channel' for a server channel, "
        "'user' for a person. Copy it from the same discovery result.",
        json_schema_extra={"enum": ["channel", "user"]},
    )
    text: str = Field(description="The message text to send.")


class SendMessage(_DiscordToolBase):
    """Send text to another Discord channel or user."""

    name: str = "SendMessage"
    description: str = """Send a text message to a Discord channel or person \
OTHER than the current conversation.

## When to Use
- The user asks you to notify or relay something to a *different* channel \
or person (e.g. "tell the #general channel ...", "let Alex know ...").

## When NOT to Use
- To answer the person you are talking with now — that reply is sent \
automatically. Never use this tool for the current conversation.

## How to Use
Obtain ``target_id`` first: a channel's via ``ListChats``, a person's via \
``ListChatMembers``. Pass ``target_id`` and ``target_type`` exactly as \
returned. Text longer than Discord's 2000-character cap is split \
automatically. Sending requires the user's confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendMessageParams.model_json_schema()

    async def __call__(
        self,
        target_id: str,
        target_type: str,
        text: str,
    ) -> ToolChunk:
        """Send ``text`` to ``target_id``.

        Args:
            target_id (`str`): Target id from a discovery result.
            target_type (`str`): ``"channel"`` or ``"user"``.
            text (`str`): The message text to send.
        """
        try:
            await self._channel.send_message_to(
                target_id,
                target_type,
                text,
            )
        except Exception as e:  # pylint: disable=broad-except
            return _sent(f"message to {target_id}", str(e))
        return _sent(f"message to {target_id}")
