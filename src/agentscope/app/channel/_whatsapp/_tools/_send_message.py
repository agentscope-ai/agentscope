# -*- coding: utf-8 -*-
"""Send WhatsApp text messages."""
from pydantic import Field

from .....tool import ToolChunk
from ._base import _WhatsAppRecipientParams, _WhatsAppToolBase, _ack


class _SendMessageParams(_WhatsAppRecipientParams):
    text: str = Field(description="The message text to send.")


class SendMessage(_WhatsAppToolBase):
    """Send text to another WhatsApp chat or person."""

    name: str = "SendMessage"
    description: str = """Send a text message to a WhatsApp chat or person \
OTHER than the current conversation.

Obtain ``to`` and ``recipient_type`` from ListChats or ListChatMembers and
copy both values verbatim. Sending requires user confirmation."""
    is_read_only: bool = False
    input_schema: dict = _SendMessageParams.model_json_schema()

    async def __call__(
        self,
        to: str,
        recipient_type: str,
        text: str,
    ) -> ToolChunk:
        """Send text to ``to`` and return a tool result."""
        result = await self._channel.send_message_to(
            to,
            recipient_type,
            text,
        )
        return _ack(result, f"message to {to}")
