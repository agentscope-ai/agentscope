# -*- coding: utf-8 -*-
"""The tool response class."""
import uuid
from dataclasses import dataclass, field
from typing import List, Literal, Self

from ..message import DataBlock, TextBlock, Base64Source


@dataclass
class ToolChunk:
    """The tool result chunk from a tool execution."""

    content: List[TextBlock | DataBlock]
    """The chunk data blocks, note for one multimodal data, the DataBlock 
    instance should have the same block id, so that the agent can group them 
    together."""

    state: Literal["error", "interrupted", "running"] = "running"
    """The execution state of the tool chunk."""

    is_last: bool = True
    """Whether this is the last response in a stream tool execution."""

    metadata: dict = field(default_factory=dict)
    """The metadata to be accessed within the agent, so that we don't need to
    parse the tool result block."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    """The identity of the tool response."""

@dataclass
class ToolResponse:
    """The tool response from a tool execution, which contains the completed
    tool result (compared to ToolChunk)."""

    content: List[TextBlock | DataBlock] = field(default_factory=list)
    """The completed tool result data blocks."""

    state: Literal["error", "interrupted", "finished"] = "finished"
    """The execution state of the tool response."""

    metadata: dict = field(default_factory=dict)
    """The metadata to be accessed within the agent, so that we don't need to 
    parse the tool result block."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    """The identity of the tool response."""

    def append_chunk(self, chunk: ToolChunk) -> Self:
        """Append a tool chunk to the current tool response, accumulate the
        data blocks and update the state and metadata."""

        # Update content blocks
        current_ids_to_index = {_.id: index for index, _ in enumerate(self.content)}
        for chunk_block in chunk.content:
            if chunk_block.id in current_ids_to_index:
                # Append to the existing block
                target_block = self.content[current_ids_to_index[chunk_block.id]]

                if isinstance(target_block, TextBlock) and isinstance(chunk_block, TextBlock):
                    target_block.text += chunk_block.text
                elif isinstance(target_block, DataBlock) and isinstance(chunk_block, DataBlock):
                    if isinstance(target_block.source, Base64Source) and isinstance(chunk_block.source, Base64Source):
                        # Accumulate the base64 data
                        target_block.source.data += chunk_block.data
                        # Update the newest media type and name if provided
                        target_block.name = chunk_block.name or target_block.name
                        target_block.source.media_type = chunk_block.media_type or chunk_block.media_type
                    else:
                        raise ValueError(
                            "Cannot append DataBlock with URL source or "
                            f"different source types: {target_block.source} vs {chunk_block.source}",
                        )
                else:
                    # For different block types with the same ID, we just
                    # append the new block with a new ID to avoid the conflict
                    chunk_block.id = uuid.uuid4().hex
                    self.content.append(chunk_block)

            else:
                # Append to the end
                self.content.append(chunk_block)

        # Update id, state and metadata
        # TODO: what's the relationship between the chunk id and response id?
        # Only reserve the failure state and keep the previous state if not
        # worse.
        if chunk.state == "error":
            self.state = "error"
        elif chunk.state == "interrupted":
            self.state = "interrupted"

        self.metadata.update(chunk.metadata)

        return self
