# -*- coding: utf-8 -*-
"""Convert ContentBlock to OpenTelemetry GenAI part format."""

from typing import Any, Dict

from ..message import (
    ContentBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    DataBlock,
    Base64Source,
    URLSource,
)

from ._utils import _serialize_to_str

# Map MIME type prefix to OTel modality name
_MEDIA_TYPE_TO_MODALITY: Dict[str, str] = {
    "image": "image",
    "audio": "audio",
    "video": "video",
}


def _get_modality(media_type: str) -> str:
    """Derive OTel modality from a MIME type string (e.g. 'image/png')."""
    prefix = media_type.split("/")[0] if media_type else ""
    return _MEDIA_TYPE_TO_MODALITY.get(prefix, "unknown")


def _convert_media_block(
    source: Base64Source | URLSource,
) -> Dict[str, Any] | None:
    """Convert a DataBlock source to OpenTelemetry GenAI part format.

    Args:
        source (`Base64Source | URLSource`):
            The data source of the DataBlock.

    Returns:
        `Dict[str, Any] | None`:
            Converted part Dictionary or None if the source type is invalid.
    """
    modality = _get_modality(source.media_type)

    if isinstance(source, URLSource):
        return {
            "type": "uri",
            "uri": str(source.url),
            "modality": modality,
        }

    if isinstance(source, Base64Source):
        return {
            "type": "blob",
            "content": source.data,
            "media_type": source.media_type,
            "modality": modality,
        }

    return None


def _convert_block_to_part(block: ContentBlock) -> Dict[str, Any] | None:
    """Convert content block to OpenTelemetry GenAI part format.

    Converts text, thinking, tool_call, tool_result and data (media) blocks
    to standardized parts.

    Args:
        block (`ContentBlock`):
            The content block object to convert. Supported block types:
            - text: Text content block
            - thinking: Reasoning/thinking content block
            - tool_call: Tool call block with id, name, and input
            - tool_result: Tool result block with id and output
            - data: Binary data block (image, audio, video, etc.)

    Returns:
        `Dict[str, Any] | None`:
            Standardized part Dictionary in OpenTelemetry GenAI format,
            or None if the block type is unsupported or cannot be converted.
    """
    part: Dict[str, Any] | None = None

    if isinstance(block, TextBlock):
        part = {
            "type": "text",
            "content": block.text,
        }
    elif isinstance(block, ThinkingBlock):
        part = {
            "type": "reasoning",
            "content": block.thinking,
        }
    elif isinstance(block, ToolCallBlock):
        part = {
            "type": "tool_call",
            "id": block.id,
            "name": block.name,
            "arguments": block.input,
        }
    elif isinstance(block, ToolResultBlock):
        output = block.output
        if isinstance(output, (list, dict)):
            result = _serialize_to_str(output)
        else:
            result = str(output)

        part = {
            "type": "tool_call_response",
            "id": block.id,
            "response": result,
        }
    elif isinstance(block, DataBlock):
        part = _convert_media_block(block.source)

    return part
