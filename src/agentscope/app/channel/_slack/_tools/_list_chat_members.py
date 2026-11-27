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
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of members to return.",
    )
    cursor: str | None = Field(
        default=None,
        description="The ``next_cursor`` of a previous ListChatMembers "
        "result for the same conversation, to fetch the next page. Omit "
        "for the first page.",
    )


class ListChatMembers(_SlackToolBase):
    """List a conversation's members as ready-to-send targets."""

    name: str = "ListChatMembers"
    description: str = """List the members of a Slack conversation, one \
page at a time, to obtain a person's id for a direct message.

## When to Use
- You need to message a *specific person* directly and must first find \
their id. Get the conversation's ``chat_id`` from ``ListChats``, then call \
this.

## Output
A JSON object ``{members, next_cursor}``. ``members`` is an array of \
``{chat_id, name}``, where ``chat_id`` is the member's user id and \
``name`` is null when it could not be looked up. Slack opens a direct \
message when you send to a user id, so copy it straight into a Send* \
tool. When ``next_cursor`` is not null there are more members: pass it \
back as ``cursor`` to fetch them."""
    is_read_only: bool = True
    input_schema: dict = _ListChatMembersParams.model_json_schema()

    async def __call__(
        self,
        chat_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolChunk:
        """Return one page of the members of ``chat_id`` as send targets.

        Args:
            chat_id (`str`): The conversation id from a ListChats result.
            limit (`int`): Maximum number of members to return.
            cursor (`str | None`): Cursor of the page to fetch.
        """
        page = await self._channel.list_chat_members(chat_id, limit, cursor)
        items = [
            {"chat_id": member["user_id"], "name": member["name"]}
            for member in page["members"]
        ]
        return ToolChunk(
            content=[
                TextBlock(
                    text=json.dumps(
                        {"members": items, "next_cursor": page["next_cursor"]},
                        ensure_ascii=False,
                    ),
                ),
            ],
        )
