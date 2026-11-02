# -*- coding: utf-8 -*-
"""ListChatMembers — discover a chat's members as address pairs."""
import json

from pydantic import Field

from .....message import TextBlock, ToolResultState
from .....tool import ParamsBase, ToolChunk
from ._base import _DiscordToolBase


class _ListChatMembersParams(ParamsBase):
    chat_id: str = Field(
        description="The channel's target_id, taken from a ListChats result.",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum server members to scan. Members who cannot "
        "view the channel are filtered out after the scan.",
    )


class ListChatMembers(_DiscordToolBase):
    """List a chat's members as ready-to-send address pairs."""

    name: str = "ListChatMembers"
    description: str = """List the members of a Discord chat, to obtain a \
person's id for a direct message.

## When to Use
- You need to message a *specific person* directly and must first find \
their id. Get the channel's ``target_id`` from ``ListChats``, then call this.

## Important Limitation
For a server channel this scans the server's member list over REST, which \
requires the **Server Members Intent** privileged setting enabled for the \
bot in the Discord developer portal. Without it the tool fails with a \
permissions error — that failure names the missing setting.

## Output
A JSON array of ``{target_id, target_type, name}``. ``target_type`` is \
always ``"user"``. For a server channel only members who can view that \
channel are listed. Copy the ``target_id`` + ``target_type`` of the person \
you want into a Send* tool to message them directly."""
    is_read_only: bool = True
    input_schema: dict = _ListChatMembersParams.model_json_schema()

    async def __call__(
        self,
        chat_id: str,
        limit: int = 100,
    ) -> ToolChunk:
        """Return the members of ``chat_id`` as address pairs.

        Args:
            chat_id (`str`): The channel's target_id from a ListChats
                result.
            limit (`int`): Maximum server members to scan.
        """
        try:
            members = await self._channel.list_chat_members(chat_id, limit)
        except Exception as e:  # pylint: disable=broad-except
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"ListChatMembers failed: {e}. Listing a "
                        "server's members requires the Server Members "
                        "Intent privileged setting for the bot in the "
                        "Discord developer portal.",
                    ),
                ],
                state=ToolResultState.ERROR,
            )
        items = [
            {
                "target_id": member.get("user_id", ""),
                "target_type": "user",
                "name": member.get("name", ""),
            }
            for member in members
        ]
        return ToolChunk(
            content=[TextBlock(text=json.dumps(items, ensure_ascii=False))],
        )
