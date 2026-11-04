# -*- coding: utf-8 -*-
"""Read a bounded range of one wiki document."""

import json

from pydantic import Field

from ._base import _WikiToolBase
from ....message import TextBlock, ToolResultState
from ....tool import ParamsBase, ToolChunk


class _ReadWikiDocumentParams(ParamsBase):
    node_id: str = Field(
        min_length=1,
        max_length=512,
        description="Node id of a document returned by ListWikiNodes.",
    )
    start_index: int = Field(
        default=0,
        ge=0,
        description="First content block to read.",
    )
    max_blocks: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Maximum number of content blocks to read.",
    )


class ReadWikiDocument(_WikiToolBase):
    """Read one wiki document as the current sender."""

    name: str = "ReadWikiDocument"
    description: str = """Read the text of a wiki document.

Use a ``node_id`` that ``ListWikiNodes`` reported with ``is_document=true``.
A long document is read in ranges: when the result carries
``next_start_index``, call again with it to continue."""
    input_schema: dict = _ReadWikiDocumentParams.model_json_schema()

    async def __call__(
        self,
        node_id: str,
        start_index: int = 0,
        max_blocks: int = 50,
    ) -> ToolChunk:
        """Read a bounded range of the document's content blocks.

        Args:
            node_id (`str`): The document to read.
            start_index (`int`): First block index to read.
            max_blocks (`int`): Maximum number of blocks to read.

        Returns:
            `ToolChunk`: The document's content, plus where to resume.
        """
        try:
            document = await self._channel.read_wiki_document(
                self._channel_user_id,
                node_id,
                start_index,
                max_blocks,
            )
        except RuntimeError as exc:
            return ToolChunk(
                content=[TextBlock(text=str(exc))],
                state=ToolResultState.ERROR,
            )
        if document is None:
            return ToolChunk(
                content=[
                    TextBlock(
                        text="That node is not a readable wiki document.",
                    ),
                ],
                state=ToolResultState.ERROR,
            )
        return ToolChunk(
            content=[
                TextBlock(
                    text=json.dumps(
                        {
                            "node_id": document.node_id,
                            "name": document.name,
                            "start_index": start_index,
                            "next_start_index": document.next_start_index,
                        },
                        ensure_ascii=False,
                    ),
                ),
                *document.content,
            ],
        )
