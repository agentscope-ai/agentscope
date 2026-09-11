# -*- coding: utf-8 -*-
"""ListChatMembers — discover a conversation's members as send targets."""
import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _SlackToolBase


class _ListChatMembersParams(ParamsBase):
    chat_id: str = Field(
        description="The conversation's id, taken from a ListChats result.",
    )


class ListChatMembers(_SlackToolBase):
    """List a conversation's members as ready-to-send targets."""

    name: str = "ListChatMembers"
    description: str = """List the members of a Slack conversation, to \
obtain a person's id for a direct message.

## When to Use
- You need to message a *specific person* directly and must first find \
their id. Get the conversation's ``chat_id`` from ``ListChats``, then call \
this.

## Output
A JSON array of ``{chat_id, name}``, where ``chat_id`` is the member's \
user id. Slack opens a direct message when you send to a user id, so copy \
it straight into a Send* tool."""
    is_read_only: bool = True
    input_schema: dict = _ListChatMembersParams.model_json_schema()

    async def __call__(self, chat_id: str) -> ToolChunk:
        """Return the members of ``chat_id`` as send targets.

        Args:
            chat_id (`str`): The conversation id from a ListChats result.
        """
        members = await self._channel.list_chat_members(chat_id)
        items = [
            {
                "chat_id": member.get("user_id", ""),
                "name": member.get("name", ""),
            }
            for member in members
        ]
        return ToolChunk(
            content=[TextBlock(text=json.dumps(items, ensure_ascii=False))],
        )
