# -*- coding: utf-8 -*-
"""Browse one level of a wiki space's tree."""

import json

from pydantic import Field

from ._base import _WikiToolBase
from ....message import TextBlock, ToolResultState
from ....tool import ParamsBase, ToolChunk


class _ListWikiNodesParams(ParamsBase):
    parent_node_id: str = Field(
        min_length=1,
        max_length=512,
        description="A space's root node id, or a folder's node id.",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Maximum number of direct children to return.",
    )
    next_token: str = Field(
        default="",
        max_length=2048,
        description="Pagination token returned by a previous call.",
    )


class ListWikiNodes(_WikiToolBase):
    """List the direct children of one wiki space root or folder."""

    name: str = "ListWikiNodes"
    description: str = """Browse a wiki space one level at a time.

Pass a ``root_node_id`` from ``ListWikiSpaces``, or a folder's ``node_id``.
An entry with ``has_children=true`` is a folder you can browse again; one
with ``is_document=true`` can be read with ``ReadWikiDocument``. Use a
returned ``next_token`` to continue the same listing."""
    input_schema: dict = _ListWikiNodesParams.model_json_schema()

    async def __call__(
        self,
        parent_node_id: str,
        limit: int = 50,
        next_token: str = "",
    ) -> ToolChunk:
        """List the children visible below ``parent_node_id``.

        Args:
            parent_node_id (`str`): Space root or folder node id.
            limit (`int`): Maximum result count.
            next_token (`str`): Optional pagination token.

        Returns:
            `ToolChunk`: JSON-encoded child entries and the next page token.
        """
        try:
            page = await self._channel.list_wiki_nodes(
                self._channel_user_id,
                parent_node_id,
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
                            "nodes": [
                                node.model_dump(mode="json")
                                for node in page.items
                            ],
                            "next_token": page.next_token or "",
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
        )
