# -*- coding: utf-8 -*-
"""SendMessage — send text to another WeCom chat/user."""
from pydantic import Field

from .....tool import ParamsBase, ToolChunk
from ._base import _WeComToolBase, _ack


class _SendMessageParams(ParamsBase):
    chat_id: str = Field(
        description="A group's chat id, or a person's WeCom userid.",
    )
    chat_type: str = Field(
        description="Must match the id: 'group' for a group chat, "
        "'single' for one person.",
        json_schema_extra={"enum": ["single", "group"]},
    )
    text: str = Field(description="The message text to send (Markdown).")


class SendMessage(_WeComToolBase):
    """Send text to another WeCom chat/user."""

    name: str = "SendMessage"
    description: str = """Send a message to a WeCom chat or person OTHER \
than the current conversation.

## When to Use
- The user asks you to notify or relay something to a *different* group or \
person (e.g. "tell the finance group ...", "let Li Si know ...").

## When NOT to Use
- To answer the person you are talking with now — that reply is sent \
automatically. Never use this tool for the current conversation.

## How to Use
WeCom exposes no directory lookup, so ``chat_id`` must come from the user \
or from a chat you have already seen. Pass ``chat_type`` matching it. \
Sending requires the user's confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendMessageParams.model_json_schema()

    async def __call__(
        self,
        chat_id: str,
        chat_type: str,
        text: str,
    ) -> ToolChunk:
        """Send ``text`` to ``chat_id``.

        Args:
            chat_id (`str`): Group chat id, or a person's userid.
            chat_type (`str`): ``"single"`` or ``"group"``.
            text (`str`): The message text to send.
        """
        data = await self._channel.send_message_to(chat_id, chat_type, text)
        return _ack(data, f"message to {chat_id}")
