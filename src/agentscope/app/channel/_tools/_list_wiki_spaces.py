# -*- coding: utf-8 -*-
"""List the wiki spaces the current platform user can read."""

import json

from pydantic import Field

from ._base import _WikiToolBase
from ....message import TextBlock, ToolResultState
from ....tool import ParamsBase, ToolChunk


class _ListWikiSpacesParams(ParamsBase):
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of spaces to return.",
    )
    next_token: str = Field(
        default="",
        max_length=2048,
        description="Pagination token returned by a previous call.",
    )


class ListWikiSpaces(_WikiToolBase):
    """List wiki spaces readable by the current sender."""

    name: str = "ListWikiSpaces"
    description: str = """List the wiki spaces (knowledge bases) the current
user can read on the chat platform.

Use a returned ``root_node_id`` with ``ListWikiNodes`` to browse a space. If
``next_token`` comes back non-empty, call again with it to get the next page.
Access is evaluated as the person who sent the current message; the model
cannot supply or change that identity."""
    input_schema: dict = _ListWikiSpacesParams.model_json_schema()

    async def __call__(
        self,
        limit: int = 20,
        next_token: str = "",
    ) -> ToolChunk:
        """List the spaces visible to the bound user.

        Args:
            limit (`int`): Maximum result count.
            next_token (`str`): Optional pagination token.

        Returns:
            `ToolChunk`: JSON-encoded spaces and the next page token.
        """
        try:
            spaces, token = await self._channel.list_wiki_spaces(
                self._channel_user_id,
                limit,
                next_token or None,
            )
        except RuntimeError as exc:
            return ToolChunk(
                content=[TextBlock(text=str(exc))],
                state=ToolResultState.ERROR,
            )
        return ToolChunk(
            content=[
                TextBlock(
                    text=json.dumps(
                        {
                            "spaces": [
                                space.model_dump(mode="json")
                                for space in spaces
                            ],
                            "next_token": token or "",
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
        )
