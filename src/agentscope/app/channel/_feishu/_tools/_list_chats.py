# -*- coding: utf-8 -*-
"""ListChats — discover the bot's Feishu groups as address pairs."""
import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _FeishuToolBase


class _ListChatsParams(ParamsBase):
    query: str | None = Field(
        default=None,
        description="Optional case-insensitive substring to filter groups "
        "by name. Omit to list all.",
    )


class ListChats(_FeishuToolBase):
    """List the bot's Feishu groups as ready-to-send address pairs."""

    name: str = "ListChats"
    description: str = """List the Feishu groups this bot belongs to, to \
obtain a target for sending.

## When to Use
- You need to message a *group* other than the current conversation and \
must first find its id.

## Output
A JSON array of ``{receive_id, receive_id_type, name}``. ``receive_id_type`` \
is always ``"chat_id"``. Copy ``receive_id`` + ``receive_id_type`` verbatim \
into a Send* tool. To reach a specific *person* in a group, take that \
group's ``receive_id`` and call ``ListChatMembers`` next."""
    is_read_only: bool = True
    input_schema: dict = _ListChatsParams.model_json_schema()

    async def __call__(self, query: str | None = None) -> ToolChunk:
        """Return the bot's chats filtered by ``query``.

        Args:
            query (`str | None`): Case-insensitive name filter, or all.
        """
        chats = await self._channel.list_bot_chats()
        needle = (query or "").lower()
        items = [
            {
                "receive_id": chat.get("chat_id", ""),
                "receive_id_type": "chat_id",
                "name": chat.get("name", ""),
            }
            for chat in chats
            if not needle or needle in (chat.get("name", "") or "").lower()
        ]
        return ToolChunk(
            content=[TextBlock(text=json.dumps(items, ensure_ascii=False))],
        )
