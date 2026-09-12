# -*- coding: utf-8 -*-
"""Search users visible in the DingTalk enterprise directory."""

import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _DingTalkToolBase


class _ListUsersParams(ParamsBase):
    query: str = Field(
        min_length=1,
        description="User name to search for in the DingTalk directory.",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of matches to return.",
    )


class ListUsers(_DingTalkToolBase):
    """Search enterprise users and return ready-to-send targets."""

    name: str = "ListUsers"
    description: str = """Search users visible to the DingTalk application.

## When to Use
- You need a stable DingTalk user target before sending a direct message.

## Important Limitation
Searching the directory needs contact permission, which the DingTalk \
application may not have been granted, and a failed lookup reads the same \
as one that matched nobody. An empty array therefore proves nothing: it \
may mean no match, no permission, or a request that did not go through. \
Ask the user for the target rather than retrying, and say the search came \
back empty rather than that the person does not exist.

## Output
A JSON array of ``{target, name, title, department_ids}``. Copy ``target`` \
verbatim into a DingTalk Send* tool."""
    is_read_only: bool = True
    input_schema: dict = _ListUsersParams.model_json_schema()

    async def __call__(
        self,
        query: str,
        limit: int = 20,
    ) -> ToolChunk:
        """Search visible users by name.

        Args:
            query (`str`): User-name search term.
            limit (`int`): Maximum result count.

        Returns:
            `ToolChunk`: JSON-encoded user target records.
        """
        users = await self._channel.search_users(query, limit)
        items = [
            {
                "target": f"user:{user.get('user_id', '')}",
                "name": user.get("name", ""),
                "title": user.get("title", ""),
                "department_ids": user.get("department_ids", []),
            }
            for user in users
            if user.get("user_id")
        ]
        return ToolChunk(
            content=[TextBlock(text=json.dumps(items, ensure_ascii=False))],
        )
