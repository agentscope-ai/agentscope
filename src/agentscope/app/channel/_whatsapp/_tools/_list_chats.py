# -*- coding: utf-8 -*-
"""List available WhatsApp recipients."""
import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _WhatsAppToolBase


class _ListChatsParams(ParamsBase):
    query: str | None = Field(
        default=None,
        description="Optional case-insensitive name filter.",
    )


class ListChats(_WhatsAppToolBase):
    """List available recipients as ready-to-send address pairs."""

    name = "ListChats"
    description: str = """List WhatsApp targets available to this bot.

Groups come from the Groups API. Individual chats are limited to contacts
observed by this running process because Cloud API has no individual-chat
directory. Copy ``to`` and ``recipient_type`` into a Send* tool."""
    is_read_only: bool = True
    input_schema: dict = _ListChatsParams.model_json_schema()

    async def __call__(self, query: str | None = None) -> ToolChunk:
        """Return the observed recipient list as JSON."""
        chats = await self._channel.list_bot_chats()
        needle = (query or "").lower()
        items = [
            {
                "to": chat.get("chat_id", ""),
                "recipient_type": (
                    "group"
                    if chat.get("chat_type") == "group"
                    else "individual"
                ),
                "name": chat.get("name", ""),
            }
            for chat in chats
            if not needle or needle in (chat.get("name", "") or "").lower()
        ]
        return ToolChunk(
            content=[TextBlock(text=json.dumps(items, ensure_ascii=False))]
        )
