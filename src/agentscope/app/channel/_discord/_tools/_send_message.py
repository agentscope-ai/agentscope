# -*- coding: utf-8 -*-
"""SendMessage — send text to a Discord channel or user."""

from pydantic import Field

from .....tool import ParamsBase, ToolChunk
from ._base import _ack, _DiscordToolBase


class _SendMessageParams(ParamsBase):
    target: str = Field(
        pattern=r"^(channel|user):\d+$",
        description="Target returned by ListChats or ListChatMembers.",
    )
    text: str = Field(min_length=1, description="Message text to send.")


class SendMessage(_DiscordToolBase):
    """Send text to a conversation other than the current one."""

    name: str = "SendMessage"
    description: str = """Send text to a Discord guild channel or user DM \
other than the current conversation.

Obtain ``target`` from ``ListChats`` or ``ListChatMembers`` and copy it \
verbatim. Replies to the current conversation are delivered automatically. \
This cross-target send requires user confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendMessageParams.model_json_schema()

    async def __call__(self, target: str, text: str) -> ToolChunk:
        """Send text to an encoded Discord target.

        Args:
            target (`str`): Encoded Discord channel or user target.
            text (`str`): Message text.

        Returns:
            `ToolChunk`: Discord acceptance result.
        """
        accepted = await self._channel.send_message_to(target, text)
        return _ack(accepted, f"message to {target}")
