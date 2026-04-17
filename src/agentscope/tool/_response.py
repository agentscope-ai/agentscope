# -*- coding: utf-8 -*-
"""The tool response class."""
import uuid
from typing import List, Literal, Self

from pydantic import BaseModel, Field

from ..message import DataBlock, TextBlock, Base64Source


class ToolChunk(BaseModel):
    """The tool result chunk from a tool execution."""

    content: List[TextBlock | DataBlock]
    """The chunk data blocks, note for one multimodal data, the DataBlock
    instance should have the same block id, so that the agent can group them
    together."""

    state: Literal["error", "interrupted", "running"] = "running"
    """The execution state of the tool chunk."""

    is_last: bool = True
    """Whether this is the last response in a stream tool execution."""

    metadata: dict = Field(default_factory=dict)
    """The metadata to be accessed within the agent, so that we don't need to
    parse the tool result block."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The identity of the tool response."""


class ToolResponse(BaseModel):
    """The tool response from a tool execution, which contains the completed
    tool result (compared to ToolChunk)."""

    content: List[TextBlock | DataBlock] = Field(default_factory=list)
    """The completed tool result data blocks."""

    state: Literal["error", "interrupted", "finished"] = "finished"
    """The execution state of the tool response."""

    metadata: dict = Field(default_factory=dict)
    """The metadata to be accessed within the agent, so that we don't need to
    parse the tool result block."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The identity of the tool response."""

    def append_chunk(self, chunk: ToolChunk) -> Self:
        """Append a tool chunk to the current tool response, accumulate the
        data blocks and update the state and metadata."""

        # Update content blocks
        current_ids_to_index = {
            _.id: index for index, _ in enumerate(self.content)
        }
        for chunk_block in chunk.content:
            if chunk_block.id in current_ids_to_index:
                # Append to the existing block
                target_block = self.content[
                    current_ids_to_index[chunk_block.id]
                ]

                if isinstance(target_block, TextBlock) and isinstance(
                    chunk_block,
                    TextBlock,
                ):
                    target_block.text += chunk_block.text
                elif isinstance(target_block, DataBlock) and isinstance(
                    chunk_block,
                    DataBlock,
                ):
                    if isinstance(
                        target_block.source,
                        Base64Source,
                    ) and isinstance(chunk_block.source, Base64Source):
                        # Accumulate the base64 data
                        target_block.source.data += chunk_block.source.data
                        # Update the newest media type and name if provided
                        target_block.name = (
                            chunk_block.name or target_block.name
                        )
                        target_block.source.media_type = (
                            chunk_block.source.media_type
                            or target_block.source.media_type
                        )
                    else:
                        raise ValueError(
                            "Cannot append DataBlock with URL source or "
                            f"different source types: {target_block.source} "
                            f"vs {chunk_block.source}",
                        )
                else:
                    # For different block types with the same ID, we just
                    # append the new block with a new ID to avoid the conflict
                    chunk_block.id = uuid.uuid4().hex
                    self.content.append(chunk_block)

            else:
                # Append a copy to avoid modifying the original chunk
                self.content.append(chunk_block.model_copy(deep=True))

        # Update id, state and metadata
        # TODO: what's the relationship between the chunk id and response id?
        # Only reserve the failure state and keep the previous state if not
        # worse.
        if chunk.state == "error":
            self.state = "error"
        elif chunk.state == "interrupted":
            self.state = "interrupted"

        self.metadata.update(chunk.metadata)

        # Post-processing: merge consecutive TextBlocks
        # DataBlocks are kept separate and only merged by explicit id matching
        merged_content: List[TextBlock | DataBlock] = []
        for block in self.content:
            if isinstance(block, TextBlock) and merged_content:
                # Check if the last block is also a TextBlock
                last_block = merged_content[-1]
                if isinstance(last_block, TextBlock):
                    # Merge consecutive TextBlocks
                    last_block.text += block.text
                else:
                    # Last block is DataBlock, append current TextBlock
                    merged_content.append(block)
            else:
                # First block or current block is DataBlock
                merged_content.append(block)

        self.content = merged_content

        return self
