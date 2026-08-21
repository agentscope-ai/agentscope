# -*- coding: utf-8 -*-
"""ListChats — discover the bot's Discord channels as address pairs."""
import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _DiscordToolBase


class _ListChatsParams(ParamsBase):
    query: str | None = Field(
        default=None,
        description="Optional case-insensitive substring to filter channels "
        "by name. Omit to list all.",
    )


class ListChats(_DiscordToolBase):
    """List the bot's Discord channels as ready-to-send address pairs."""

    name: str = "ListChats"
    description: str = """List the Discord text channels this bot can see, to \
obtain a target for sending.

## When to Use
- You need to message a *channel* other than the current conversation and \
must first find its id.

## Output
A JSON array of ``{chat_id, name}``. Copy ``chat_id`` verbatim into a Send* \
tool with ``target`` set to ``"channel"``. To reach a specific *person* in a \
server, take that channel's ``chat_id`` and call ``ListChatMembers`` next."""
    is_read_only: bool = True
    input_schema: dict = _ListChatsParams.model_json_schema()

    async def __call__(self, query: str | None = None) -> ToolChunk:
        """Return the bot's channels filtered by ``query``.

        Args:
            query (`str | None`): Case-insensitive name filter, or all.
        """
        chats = await self._channel.list_bot_chats()
        needle = (query or "").lower()
        items = [
            {
                "chat_id": chat.get("chat_id", ""),
                "name": chat.get("name", ""),
            }
            for chat in chats
            if not needle or needle in (chat.get("name", "") or "").lower()
        ]
        return ToolChunk(
            content=[TextBlock(text=json.dumps(items, ensure_ascii=False))],
        )
