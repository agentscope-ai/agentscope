# -*- coding: utf-8 -*-
"""Send WhatsApp image attachments."""
from .....tool import ToolChunk
from ._base import _WhatsAppMediaParams, _WhatsAppMediaTool


class SendImage(_WhatsAppMediaTool):
    """Send a workspace image to another WhatsApp chat or person."""

    name: str = "SendImage"
    description: str = """Send a workspace image to a WhatsApp chat or person \
OTHER than the current conversation.

Obtain ``to`` and ``recipient_type`` from ListChats or ListChatMembers and
copy both values verbatim. Sending requires user confirmation."""
    input_schema: dict = _WhatsAppMediaParams.model_json_schema()

    async def __call__(
        self,
        path: str,
        to: str,
        recipient_type: str,
    ) -> ToolChunk:
        """Read a workspace image and send it to ``to``."""
        return await self._send_media(path, to, recipient_type)

    async def _send_bytes(
        self,
        to: str,
        recipient_type: str,
        data: bytes,
        name: str,
    ) -> dict | None:
        """Send image bytes through the bound channel."""
        return await self._channel.send_image_to(
            to,
            recipient_type,
            data,
            name,
        )

    def _success_description(self, name: str, to: str) -> str:
        """Return the successful image-send description."""
        del name
        return f"image to {to}"
