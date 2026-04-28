# -*- coding: utf-8 -*-
"""Convert ContentBlock to OpenTelemetry GenAI part format."""

from typing import Any

from ..message import ContentBlock

from ._utils import _serialize_to_str


def _convert_media_block(
    source: dict[str, Any],
    modality: str,
) -> dict[str, Any] | None:
    """Convert media block (image/audio/video) to OpenTelemetry format.

    Args:
        source (`dict[str, Any]`):
            Source Dictionary with type, url/data, and media_type.
        modality (`str`):
            Media modality: "image", "audio", or "video".

    Returns:
        `dict[str, Any] | None`:
            Converted part Dictionary or None if source type is invalid.
    """
    source_type = source.get("type")

    if source_type == "url":
        url = source.get("url", "")
        return {
            "type": "uri",
            "uri": url,
            "modality": modality,
        }

    if source_type == "base64":
        data = source.get("data", "")
        media_type = source.get("media_type")
        if not media_type:
            default_media_types = {
                "image": "image/jpeg",
                "audio": "audio/wav",
                "video": "video/mp4",
            }
            media_type = default_media_types.get(modality, "unknown")
        return {
            "type": "blob",
            "content": data,
            "media_type": media_type,
            "modality": modality,
        }

    return None


def _convert_block_to_part(block: ContentBlock) -> dict[str, Any] | None:
    """Convert content block to OpenTelemetry GenAI part format.

    Converts text, thinking, tool_use, tool_result, image, audio, video
    blocks to standardized parts.

    Args:
        block (`ContentBlock`):
            The content block object to convert. Supported block types:
            - text: Text content block
            - thinking: Reasoning/thinking content block
            - tool_use: Tool call block with id, name, and input
            - tool_result: Tool result block with id and output
            - image: Image block with source (url or base64)
            - audio: Audio block with source (url or base64)
            - video: Video block with source (url or base64)

    Returns:
        `dict[str, Any] | None`:
            Standardized part Dictionary in OpenTelemetry GenAI format,
            or None if the block type is invalid or cannot be converted.
    """
    block_type = block.get("type")
    part: dict[str, Any] | None = None

    # Handle simple text-based blocks
    if block_type == "text":
        part = {
            "type": "text",
            "content": block.get("text", ""),
        }
    elif block_type == "thinking":
        part = {
            "type": "reasoning",
            "content": block.get("thinking", ""),
        }
    # Handle tool blocks
    elif block_type == "tool_use":
        part = {
            "type": "tool_call",
            "id": block.get("id", ""),
            "name": block.get("name", ""),
            "arguments": block.get("input", {}),
        }
    elif block_type == "tool_result":
        output = block.get("output", "")
        if isinstance(output, (list, dict)):
            result = _serialize_to_str(output)
        else:
            result = str(output)

        part = {
            "type": "tool_call_response",
            "id": block.get("id", ""),
            "response": result,
        }
    # Handle media blocks (image, audio, video)
    elif block_type in ("image", "audio", "video"):
        source = block.get("source", {})
        # Type assertion for mypy
        if isinstance(source, dict):
            source_dict: dict[str, Any] = source

            part = _convert_media_block(
                source_dict,
                modality=block_type,
            )

    return part
