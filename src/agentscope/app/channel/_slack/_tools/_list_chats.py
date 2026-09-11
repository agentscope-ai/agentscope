# -*- coding: utf-8 -*-
"""ListChats — discover the bot's Slack conversations as send targets."""
import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _SlackToolBase


class _ListChatsParams(ParamsBase):
    query: str | None = Field(
        default=None,
        description="Optional case-insensitive substring to filter "
        "conversations by name. Omit to list all.",
    )


class ListChats(_SlackToolBase):
    """List the bot's Slack conversations as ready-to-send targets."""

    name: str = "ListChats"
    description: str = """List the Slack conversations this bot can reach, \
to obtain a target for sending.

## When to Use
- You need to message a *channel* other than the current conversation and \
must first find its id.

## Output
A JSON array of ``{chat_id, name, chat_type}``. Copy ``chat_id`` verbatim \
into a Send* tool. To reach a specific *person*, take a channel's \
``chat_id`` and call ``ListChatMembers`` next."""
    is_read_only: bool = True
    input_schema: dict = _ListChatsParams.model_json_schema()

    async def __call__(self, query: str | None = None) -> ToolChunk:
        """Return the bot's conversations filtered by ``query``.

        Args:
            query (`str | None`): Case-insensitive name filter, or all.
        """
        chats = await self._channel.list_bot_chats()
        needle = (query or "").lower()
        items = [
            chat
            for chat in chats
            if not needle or needle in (chat.get("name", "") or "").lower()
        ]
        return ToolChunk(
            content=[TextBlock(text=json.dumps(items, ensure_ascii=False))],
        )
