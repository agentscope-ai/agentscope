# -*- coding: utf-8 -*-
"""ListChatMembers — discover a group's members as address pairs."""
import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _FeishuToolBase


class _ListChatMembersParams(ParamsBase):
    chat_id: str = Field(
        description="The group's chat_id, taken from a ListChats result.",
    )


class ListChatMembers(_FeishuToolBase):
    """List a group's members as ready-to-send address pairs."""

    name: str = "ListChatMembers"
    description: str = """List the members of a Feishu group, to obtain a \
person's id for a direct message.

## When to Use
- You need to message a *specific person* directly and must first find \
their id. Get the group's ``chat_id`` from ``ListChats``, then call this.

## Output
A JSON array of ``{receive_id, receive_id_type, name}``. ``receive_id_type`` \
is always ``"open_id"``. Copy the ``receive_id`` + ``receive_id_type`` of \
the person you want into a Send* tool to message them directly."""
    is_read_only: bool = True
    input_schema: dict = _ListChatMembersParams.model_json_schema()

    async def __call__(self, chat_id: str) -> ToolChunk:
        """Return the members of ``chat_id`` as address pairs.

        Args:
            chat_id (`str`): The group's chat_id from a ListChats result.
        """
        members = await self._channel.list_chat_members(chat_id)
        items = [
            {
                "receive_id": member.get("open_id", ""),
                "receive_id_type": "open_id",
                "name": member.get("name", ""),
            }
            for member in members
        ]
        return ToolChunk(
            content=[TextBlock(text=json.dumps(items, ensure_ascii=False))],
        )
