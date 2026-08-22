# -*- coding: utf-8 -*-
"""Send WhatsApp document attachments."""
from .....tool import ToolChunk
from ._base import _WhatsAppMediaParams, _WhatsAppMediaTool


class SendFile(_WhatsAppMediaTool):
    """Send a workspace file to another WhatsApp chat or person."""

    name: str = "SendFile"
    description: str = """Send a workspace file to a WhatsApp chat or person \
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
        """Read a workspace file and send it to ``to``."""
        return await self._send_media(path, to, recipient_type)

    async def _send_bytes(
        self,
        to: str,
        recipient_type: str,
        data: bytes,
        name: str,
    ) -> dict | None:
        """Send document bytes through the bound channel."""
        return await self._channel.send_file_to(
            to,
            recipient_type,
            data,
            name,
        )

    def _success_description(self, name: str, to: str) -> str:
        """Return the successful file-send description."""
        return f"file {name} to {to}"
