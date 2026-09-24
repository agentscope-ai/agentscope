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
        "this page's conversations by name. Omit to list all.",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of conversations to fetch.",
    )
    cursor: str | None = Field(
        default=None,
        description="The ``next_cursor`` of a previous ListChats result, "
        "to fetch the next page. Omit for the first page.",
    )


class ListChats(_SlackToolBase):
    """List the bot's Slack conversations as ready-to-send targets."""

    name: str = "ListChats"
    description: str = """List the Slack conversations this bot is a member \
of, one page at a time, to obtain a target for sending.

## When to Use
- You need to message a *channel* other than the current conversation and \
must first find its id.

## Output
A JSON object ``{chats, next_cursor}``. ``chats`` is an array of \
``{chat_id, name, chat_type}``; copy ``chat_id`` verbatim into a Send* \
tool. To reach a specific *person*, take a channel's ``chat_id`` and call \
``ListChatMembers`` next. When ``next_cursor`` is not null there are more \
conversations: pass it back as ``cursor`` to fetch them. ``query`` filters \
only the page it came with, so no match is not conclusive while \
``next_cursor`` is set."""
    is_read_only: bool = True
    input_schema: dict = _ListChatsParams.model_json_schema()

    async def __call__(
        self,
        query: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ToolChunk:
        """Return one page of the bot's conversations filtered by ``query``.

        Args:
            query (`str | None`): Case-insensitive name filter, or all.
            limit (`int`): Maximum number of conversations to fetch.
            cursor (`str | None`): Cursor of the page to fetch.
        """
        page = await self._channel.list_chats_page(limit, cursor)
        needle = (query or "").lower()
        items = [
            chat
            for chat in page["chats"]
            if not needle or needle in (chat.get("name", "") or "").lower()
        ]
        return ToolChunk(
            content=[
                TextBlock(
                    text=json.dumps(
                        {"chats": items, "next_cursor": page["next_cursor"]},
                        ensure_ascii=False,
                    ),
                ),
            ],
        )
