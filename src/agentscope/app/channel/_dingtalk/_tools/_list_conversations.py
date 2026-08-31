# -*- coding: utf-8 -*-
"""List conversations observed by the DingTalk robot process."""

import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _DingTalkToolBase


class _ListConversationsParams(ParamsBase):
    query: str | None = Field(
        default=None,
        description="Optional case-insensitive name filter.",
    )


class ListConversations(_DingTalkToolBase):
    """List DingTalk conversations previously observed by this robot."""

    name: str = "ListConversations"
    description: str = """List DingTalk conversations this robot process has \
already received messages from.

## Important Limitation
DingTalk application robots cannot enumerate every group they belong to, \
and the process answering this call is not the one holding the robot's \
connection — so in a deployment that separates them this list is empty \
and stays empty. Treat an empty array as the normal case and ask the user \
for the target; waiting for it to fill will not help.

## Output
A JSON array of ``{target, name, chat_type}``. Copy ``target`` verbatim into \
a DingTalk Send* tool."""
    is_read_only: bool = True
    input_schema: dict = _ListConversationsParams.model_json_schema()

    async def __call__(self, query: str | None = None) -> ToolChunk:
        """Return observed conversations filtered by name.

        Args:
            query (`str | None`): Optional case-insensitive name filter.

        Returns:
            `ToolChunk`: JSON-encoded address records.
        """
        chats = await self._channel.list_bot_chats()
        needle = (query or "").lower()
        items = [
            {
                "target": chat.get("chat_id", ""),
                "name": chat.get("name", ""),
                "chat_type": chat.get("chat_type", ""),
            }
            for chat in chats
            if not needle or needle in (chat.get("name", "") or "").lower()
        ]
        return ToolChunk(
            content=[TextBlock(text=json.dumps(items, ensure_ascii=False))],
        )
