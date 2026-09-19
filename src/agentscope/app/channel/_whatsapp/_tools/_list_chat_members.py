# -*- coding: utf-8 -*-
"""List WhatsApp group participants."""
import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _WhatsAppToolBase


class _ListChatMembersParams(ParamsBase):
    chat_id: str = Field(
        description="A Groups API group id copied from ListChats.",
    )


class ListChatMembers(_WhatsAppToolBase):
    """List a Groups API group's participants as address pairs."""

    name: str = "ListChatMembers"
    description: str = """List the participants of a WhatsApp Groups API \
group.

Use a group ``to`` value from ListChats as ``chat_id``. The output contains
``to``, ``recipient_type`` and ``name`` values ready for Send* tools."""
    is_read_only: bool = True
    input_schema: dict = _ListChatMembersParams.model_json_schema()

    async def __call__(self, chat_id: str) -> ToolChunk:
        """Return the members of ``chat_id`` as address pairs."""
        members = await self._channel.list_chat_members(chat_id)
        items = [
            {
                "to": member.get("id", ""),
                "recipient_type": "individual",
                "name": member.get("name", ""),
            }
            for member in members
        ]
        return ToolChunk(
            content=[TextBlock(text=json.dumps(items, ensure_ascii=False))],
        )
