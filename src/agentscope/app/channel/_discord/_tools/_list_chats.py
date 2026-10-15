# -*- coding: utf-8 -*-
"""ListChats — discover Discord guild text-channel targets."""

import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _DiscordToolBase


class _ListChatsParams(ParamsBase):
    query: str | None = Field(
        default=None,
        description="Optional case-insensitive substring to filter channels "
        "by their guild and channel name.",
    )


class ListChats(_DiscordToolBase):
    """List Discord guild text channels as ready-to-send targets."""

    name: str = "ListChats"
    description: str = """List Discord guild text channels visible to this \
bot.

Use this before sending to a server channel other than the current \
conversation. The output is a JSON array of ``{target, name}``; copy the \
``channel:<id>`` target verbatim into a Send* tool. Discord does not expose \
an API for enumerating every existing DM channel."""
    is_read_only: bool = True
    input_schema: dict = _ListChatsParams.model_json_schema()

    async def __call__(self, query: str | None = None) -> ToolChunk:
        """Return visible guild channels filtered by ``query``.

        Args:
            query (`str | None`): Optional case-insensitive name filter.

        Returns:
            `ToolChunk`: JSON-encoded Discord channel targets.
        """
        chats = await self._channel.list_bot_chats()
        needle = (query or "").lower()
        items = [
            {
                "target": f"channel:{chat.get('chat_id', '')}",
                "name": chat.get("name", ""),
            }
            for chat in chats
            if not needle or needle in (chat.get("name", "") or "").lower()
        ]
        return ToolChunk(
            content=[TextBlock(text=json.dumps(items, ensure_ascii=False))],
        )
