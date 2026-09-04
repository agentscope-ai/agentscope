# -*- coding: utf-8 -*-
"""ListChatMembers — discover visible Discord member targets."""

import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _DiscordToolBase


class _ListChatMembersParams(ParamsBase):
    target: str = Field(
        pattern=r"^channel:\d+$",
        description="Channel target returned by ListChats.",
    )


class ListChatMembers(_DiscordToolBase):
    """List users who can see a Discord guild text channel."""

    name: str = "ListChatMembers"
    description: str = """List members who can view one Discord guild text \
channel.

Pass a ``channel:<id>`` target returned by ``ListChats``. The output is a \
JSON array of ``{target, name}``; copy a ``user:<id>`` target into a Send* \
tool for a direct message. Discord requires the privileged members intent \
for this lookup; authorization errors are returned as tool errors."""
    is_read_only: bool = True
    input_schema: dict = _ListChatMembersParams.model_json_schema()

    async def __call__(self, target: str) -> ToolChunk:
        """Return members visible in the selected guild channel.

        Args:
            target (`str`): ``channel:<id>`` from ``ListChats``.

        Returns:
            `ToolChunk`: JSON-encoded Discord user targets.
        """
        members = await self._channel.list_chat_members(
            target.split(":", 1)[1],
        )
        items = [
            {
                "target": f"user:{member.get('user_id', '')}",
                "name": member.get("name", ""),
            }
            for member in members
        ]
        return ToolChunk(
            content=[TextBlock(text=json.dumps(items, ensure_ascii=False))],
        )
