# -*- coding: utf-8 -*-
"""ListChatMembers — discover a channel's members as address pairs."""
import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _DiscordToolBase


class _ListChatMembersParams(ParamsBase):
    chat_id: str = Field(
        description="The channel's chat_id, taken from a ListChats result.",
    )


class ListChatMembers(_DiscordToolBase):
    """List a channel's members as ready-to-send address pairs."""

    name: str = "ListChatMembers"
    description: str = """List the members of a Discord channel, to obtain a \
person's id for a direct message.

## When to Use
- You need to message a *specific person* directly and must first find \
their id. Get the channel's ``chat_id`` from ``ListChats``, then call this.

## Notes
- Server member listing requires the privileged ``GUILD_MEMBERS`` intent to \
be enabled for the bot in the Discord developer portal; otherwise only the \
recipient of a DM channel is returned.

## Output
A JSON array of ``{user_id, name}``. Copy the ``user_id`` of the person you \
want into a Send* tool with ``target`` set to ``"user"`` to message them \
directly."""
    is_read_only: bool = True
    input_schema: dict = _ListChatMembersParams.model_json_schema()

    async def __call__(self, chat_id: str) -> ToolChunk:
        """Return the members of ``chat_id`` as address pairs.

        Args:
            chat_id (`str`): The channel's chat_id from a ListChats result.
        """
        members = await self._channel.list_chat_members(chat_id)
        items = [
            {
                "user_id": member.get("user_id", ""),
                "name": member.get("name", ""),
            }
            for member in members
        ]
        return ToolChunk(
            content=[TextBlock(text=json.dumps(items, ensure_ascii=False))],
        )
