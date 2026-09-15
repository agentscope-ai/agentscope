# -*- coding: utf-8 -*-
"""SendMessage — send text to another Feishu chat/user."""
from pydantic import Field

from .....tool import ParamsBase, ToolChunk
from ._base import _FeishuToolBase, _ack


class _SendMessageParams(ParamsBase):
    receive_id: str = Field(
        description="Target id, taken verbatim from a ListChats / "
        "ListChatMembers result.",
    )
    receive_id_type: str = Field(
        description="Must match the id: 'chat_id' for a group, 'open_id' "
        "for a person. Copy it from the same discovery result.",
        json_schema_extra={"enum": ["chat_id", "open_id"]},
    )
    text: str = Field(description="The message text to send.")


class SendMessage(_FeishuToolBase):
    """Send text to another Feishu chat/user."""

    name: str = "SendMessage"
    description: str = """Send a text message to a Feishu chat or person \
OTHER than the current conversation.

## When to Use
- The user asks you to notify or relay something to a *different* group or \
person (e.g. "tell the finance group ...", "let Li Si know ...").

## When NOT to Use
- To answer the person you are talking with now — that reply is sent \
automatically. Never use this tool for the current conversation.

## How to Use
Obtain ``receive_id`` first: a group's via ``ListChats``, a person's via \
``ListChatMembers``. Pass ``receive_id`` and ``receive_id_type`` exactly as \
returned. Sending requires the user's confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendMessageParams.model_json_schema()

    async def __call__(
        self,
        receive_id: str,
        receive_id_type: str,
        text: str,
    ) -> ToolChunk:
        """Send ``text`` to ``receive_id``.

        Args:
            receive_id (`str`): Target id from a discovery result.
            receive_id_type (`str`): ``"chat_id"`` or ``"open_id"``.
            text (`str`): The message text to send.
        """
        data = await self._channel.send_message_to(
            receive_id,
            receive_id_type,
            text,
        )
        return _ack(data, f"message to {receive_id}")
