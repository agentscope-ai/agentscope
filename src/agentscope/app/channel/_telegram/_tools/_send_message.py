# -*- coding: utf-8 -*-
"""SendMessage — send text to a known Telegram chat."""
from pydantic import Field

from .....tool import ParamsBase, ToolChunk
from ._base import _ack, _TelegramToolBase


class _SendMessageParams(ParamsBase):
    chat_id: str = Field(
        description="A known numeric Telegram chat ID or @channel username.",
    )
    text: str = Field(description="The text to send.")


class SendMessage(_TelegramToolBase):
    """Send text to a Telegram chat other than the current conversation."""

    name: str = "SendMessage"
    description: str = """Send text to a known Telegram chat.

Use this only when the user asks you to notify a different chat. Replies to
the current conversation are delivered automatically. Telegram cannot list
all chats a bot belongs to, so ``chat_id`` must be supplied by the user or
already known. Sending requires user approval."""
    input_schema: dict = _SendMessageParams.model_json_schema()

    async def __call__(self, chat_id: str, text: str) -> ToolChunk:
        result = await self._channel.send_message_to(chat_id, text)
        return _ack(result, f"message to {chat_id}")
