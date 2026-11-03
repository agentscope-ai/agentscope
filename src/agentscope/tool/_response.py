# -*- coding: utf-8 -*-
"""The tool response class."""
import base64
import binascii
from typing import List, Literal, Self

from pydantic import BaseModel, Field

from .._utils._common import _generate_id
from ..message import DataBlock, TextBlock, Base64Source, ToolResultState


def _merge_base64_chunks(existing: str, incoming: str) -> str:
    """Merge independently encoded base64 chunks without corrupting padding."""
    try:
        merged = base64.b64decode(
            existing,
            validate=True,
        ) + base64.b64decode(incoming, validate=True)
    except (binascii.Error, ValueError):
        # Keep compatibility with callers/tests that used placeholder strings
        # instead of valid base64 payloads.
        return existing + incoming

    return base64.b64encode(merged).decode("ascii")


class ToolChunk(BaseModel):
    """The tool result chunk from a tool execution."""

    content: List[TextBlock | DataBlock]
    """The chunk data blocks, note for one multimodal data, the DataBlock
    instance should have the same block id, so that the agent can group them
    together."""

    state: ToolResultState = ToolResultState.RUNNING
    """The execution state of the tool chunk."""

    is_last: bool = True
    """Whether this is the last response in a stream tool execution."""

    metadata: dict = Field(default_factory=dict)
    """The metadata to be accessed within the agent, so that we don't need to
    parse the tool result block."""

    id: str = Field(default_factory=_generate_id)
    """The identity of the tool chunk."""


class ToolResponse(BaseModel):
    """The tool response from a tool execution, which contains the completed
    tool result (compared to ToolChunk)."""

    content: List[TextBlock | DataBlock] = Field(default_factory=list)
    """The completed tool result data blocks."""

    state: Literal[
        ToolResultState.ERROR,
        ToolResultState.DENIED,
        ToolResultState.INTERRUPTED,
        ToolResultState.SUCCESS,
    ] = ToolResultState.SUCCESS
    """The execution state of the tool response."""

    metadata: dict = Field(default_factory=dict)
    """The metadata to be accessed within the agent, so that we don't need to
    parse the tool result block."""

    id: str = Field(default_factory=_generate_id)
    """The identity of the tool response."""

    def append_chunk(self, chunk: ToolChunk) -> Self:
        """Append a tool chunk to the current tool response, accumulate the
        data blocks and update the state and metadata."""

        # URL attachments stand alone, even when they share a stream ID.
        streams = {
            (block.id, block.type): block
            for block in self.content
            if block.type == "text" or block.source.type == "base64"
        }
        for block in chunk.content:
            if block.type == "data" and block.source.type == "url":
                self.content.append(block.model_copy(deep=True))
                continue

            key = (block.id, block.type)
            target = streams.get(key)
            if target is None:
                target = block.model_copy(deep=True)
                self.content.append(target)
                streams[key] = target
            elif isinstance(block, TextBlock):
                assert isinstance(target, TextBlock)
                target.text += block.text
            else:
                assert isinstance(target, DataBlock)
                assert isinstance(target.source, Base64Source)
                assert isinstance(block.source, Base64Source)
                target.source.data = _merge_base64_chunks(
                    target.source.data,
                    block.source.data,
                )
                target.name = block.name or target.name
                target.source.media_type = (
                    block.source.media_type or target.source.media_type
                )

        # Update id, state and metadata
        # Only reserve the failure state and keep the previous state if not
        # worse.
        if chunk.state == ToolResultState.ERROR:
            self.state = ToolResultState.ERROR
        elif (
            self.state != ToolResultState.ERROR
            and chunk.state == ToolResultState.INTERRUPTED
        ):
            self.state = ToolResultState.INTERRUPTED
        elif (
            self.state != ToolResultState.ERROR
            and chunk.state == ToolResultState.DENIED
        ):
            self.state = ToolResultState.DENIED

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
