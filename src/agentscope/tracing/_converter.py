# -*- coding: utf-8 -*-
"""Message converter utilities for OpenTelemetry tracing.

This module provides utilities to convert messages from different formats
(AgentScope Msg objects, formatter outputs) into OpenTelemetry GenAI format.
"""
import json
from typing import Any, Optional, TYPE_CHECKING

from ._utils import _serialize_to_str

if TYPE_CHECKING:
    from ..message import (
        ContentBlock,
    )
else:
    ContentBlock = "ContentBlock"


# pylint: disable=R0912
# pylint: disable=R0915
def _convert_block_to_part(block: ContentBlock) -> Optional[dict[str, Any]]:
    """Convert content block to OpenTelemetry GenAI part format.

    Converts text, thinking, tool_use, tool_result, image, audio, video
    blocks to standardized parts.

    Args:
        block: ContentBlock object

    Returns:
        Optional[dict[str, Any]]: Standardized part dict or None
    """
    block_type = block.get("type")

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

    elif block_type == "image":
        source = block.get("source", {})
        source_type = source.get("type")

        if source_type == "url":
            url = source.get("url", "")
            part = {
                "type": "uri",
                "uri": url,
                "modality": "image",
            }
        elif source_type == "base64":
            data = source.get("data", "")
            media_type = source.get("media_type", "image/jpeg")
            part = {
                "type": "blob",
                "content": data,
                "media_type": media_type,
                "modality": "image",
            }
        else:
            part = None

    elif block_type == "audio":
        source = block.get("source", {})
        source_type = source.get("type")

        if source_type == "url":
            url = source.get("url", "")
            part = {
                "type": "uri",
                "uri": url,
                "modality": "audio",
            }
        elif source_type == "base64":
            data = source.get("data", "")
            media_type = source.get("media_type", "audio/wav")
            part = {
                "type": "blob",
                "content": data,
                "media_type": media_type,
                "modality": "audio",
            }
        else:
            part = None

    elif block_type == "video":
        source = block.get("source", {})
        source_type = source.get("type")

        if source_type == "url":
            url = source.get("url", "")
            part = {
                "type": "uri",
                "uri": url,
                "modality": "video",
            }
        elif source_type == "base64":
            data = source.get("data", "")
            media_type = source.get("media_type", "video/mp4")
            part = {
                "type": "blob",
                "content": data,
                "media_type": media_type,
                "modality": "video",
            }
        else:
            part = None

    else:
        part = None

    return part


# pylint: disable=R0912
# pylint: disable=R0915
def _parse_content_block(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse a single content block into parts.

    Args:
        block: Content block from formatter output

    Returns:
        list[dict[str, Any]]: List of parts (may be empty or have 1 item)
    """
    parts = []
    block_type = block.get("type")

    # Blocks with "type" field (OpenAI/Anthropic/DeepSeek)
    if block_type == "text" and "text" in block:
        parts.append(
            {
                "type": "text",
                "content": block.get("text", ""),
            },
        )
    elif block_type == "image_url":
        # OpenAI image: {"type": "image_url", "image_url": {"url": "..."}}
        url = block.get("image_url", {}).get("url", "")
        if url.startswith("data:"):
            media_type = "image/jpeg"
            if ";" in url:
                media_type = url.split(";")[0].split(":")[1]
            parts.append(
                {
                    "type": "blob",
                    "content": (url.split(",", 1)[-1] if "," in url else ""),
                    "media_type": media_type,
                    "modality": "image",
                },
            )
        else:
            parts.append(
                {
                    "type": "uri",
                    "uri": url,
                    "modality": "image",
                },
            )
    elif block_type == "input_audio":
        # OpenAI audio: {"type": "input_audio", "input_audio": {...}}
        audio_data = block.get("input_audio", {})
        if "data" in audio_data:
            format_str = audio_data.get("format", "wav")
            if "/" not in format_str:
                media_type = f"audio/{format_str}"
            else:
                media_type = format_str
            parts.append(
                {
                    "type": "blob",
                    "content": audio_data.get("data", ""),
                    "media_type": media_type,
                    "modality": "audio",
                },
            )
        elif "url" in audio_data:
            parts.append(
                {
                    "type": "uri",
                    "uri": audio_data.get("url", ""),
                    "modality": "audio",
                },
            )
    elif block_type == "thinking":
        # Anthropic thinking: {"type": "thinking", "thinking": "..."}
        parts.append(
            {
                "type": "reasoning",
                "content": block.get("thinking", ""),
            },
        )
    elif block_type == "image":
        # Anthropic image: {"type": "image", "source": {...}}
        source = block.get("source", {})
        source_type = source.get("type")
        if source_type == "url":
            parts.append(
                {
                    "type": "uri",
                    "uri": source.get("url", ""),
                    "modality": "image",
                },
            )
        elif source_type == "base64":
            parts.append(
                {
                    "type": "blob",
                    "content": source.get("data", ""),
                    "media_type": source.get("media_type", "image/jpeg"),
                    "modality": "image",
                },
            )
    elif block_type == "tool_use":
        # Anthropic tool_use: {"type": "tool_use", "id": "...", ...}
        parts.append(
            {
                "type": "tool_call",
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "arguments": block.get("input", {}),
            },
        )
    elif block_type == "tool_result":
        # Anthropic tool_result: {"type": "tool_result", ...}
        tool_use_id = block.get("tool_use_id", "")
        content_blocks = block.get("content", [])
        parts.append(
            {
                "type": "tool_call_response",
                "id": tool_use_id,
                "response": _serialize_to_str(content_blocks),
            },
        )
    # Blocks without "type" field (DashScope/Gemini)
    elif "text" in block and "type" not in block:
        # DashScope/Gemini text: {"text": "..."}
        text_content = block.get("text")
        if text_content is not None:
            parts.append(
                {
                    "type": "text",
                    "content": text_content,
                },
            )
    elif "image" in block and "type" not in block:
        # DashScope image: {"image": "file://..."|"data:..."|"https://..."}
        image_url = block.get("image", "")
        if image_url.startswith("data:"):
            media_type = "image/jpeg"
            if ";" in image_url:
                media_type = image_url.split(";")[0].split(":")[1]
            parts.append(
                {
                    "type": "blob",
                    "content": (
                        image_url.split(",", 1)[-1] if "," in image_url else ""
                    ),
                    "media_type": media_type,
                    "modality": "image",
                },
            )
        elif image_url.startswith("file://"):
            parts.append(
                {
                    "type": "uri",
                    "uri": image_url,
                    "modality": "image",
                },
            )
        else:
            parts.append(
                {
                    "type": "uri",
                    "uri": image_url,
                    "modality": "image",
                },
            )
    elif "audio" in block and "type" not in block:
        # DashScope audio: {"audio": "file://..."|"data:..."|"https://..."}
        audio_url = block.get("audio", "")
        if audio_url.startswith("data:"):
            media_type = "audio/wav"
            if ";" in audio_url:
                media_type = audio_url.split(";")[0].split(":")[1]
            parts.append(
                {
                    "type": "blob",
                    "content": (
                        audio_url.split(",", 1)[-1] if "," in audio_url else ""
                    ),
                    "media_type": media_type,
                    "modality": "audio",
                },
            )
        elif audio_url.startswith("file://"):
            parts.append(
                {
                    "type": "uri",
                    "uri": audio_url,
                    "modality": "audio",
                },
            )
        else:
            parts.append(
                {
                    "type": "uri",
                    "uri": audio_url,
                    "modality": "audio",
                },
            )
    elif "function_call" in block:
        # Gemini function_call: {"function_call": {...}}
        func_call = block.get("function_call", {})
        parts.append(
            {
                "type": "tool_call",
                "id": func_call.get("id", ""),
                "name": func_call.get("name", ""),
                "arguments": func_call.get("args", {}),
            },
        )
    elif "function_response" in block:
        # Gemini function_response: {"function_response": {...}}
        func_resp = block.get("function_response", {})
        response_content = func_resp.get("response", {})
        if isinstance(response_content, dict):
            result_text = response_content.get("output", "")
        else:
            result_text = str(response_content)
        parts.append(
            {
                "type": "tool_call_response",
                "id": func_resp.get("id", ""),
                "response": result_text,
            },
        )
    elif "inline_data" in block:
        # Gemini inline_data: {"inline_data": {"mime_type": "...", ...}}
        inline_data = block.get("inline_data", {})
        mime_type = inline_data.get("mime_type", "")
        data = inline_data.get("data", "")

        if mime_type.startswith("image/"):
            parts.append(
                {
                    "type": "blob",
                    "content": data,
                    "media_type": mime_type,
                    "modality": "image",
                },
            )
        elif mime_type.startswith("audio/"):
            parts.append(
                {
                    "type": "blob",
                    "content": data,
                    "media_type": mime_type,
                    "modality": "audio",
                },
            )
        elif mime_type.startswith("video/"):
            parts.append(
                {
                    "type": "blob",
                    "content": data,
                    "media_type": mime_type,
                    "modality": "video",
                },
            )

    return parts


# pylint: disable=R0912
def _convert_formatted_message_to_parts(
    formatted_msg: dict[str, Any],
) -> dict[str, Any]:
    """Convert formatter output to OpenTelemetry GenAI parts format.

    Supports: OpenAI, Anthropic, Gemini, DashScope, Ollama, DeepSeek.

    Args:
        formatted_msg: Formatted message dict from formatter output

    Returns:
        dict[str, Any]: Message dict with parts in GenAI format
    """
    try:
        parts = []
        role = formatted_msg.get("role", None)

        # Map Gemini "model" role to "assistant"
        if role == "model":
            role = "assistant"

        # Get content/parts (Gemini uses "parts", others use "content")
        content = formatted_msg.get("content")
        if content is None and "parts" in formatted_msg:
            content = formatted_msg.get("parts")

        # Handle tool role (OpenAI/DashScope/DeepSeek/Ollama)
        # Content is string: {"role": "tool", "tool_call_id": "...",
        #                     "content": "...", "name": "..."}
        if role == "tool":
            tool_call_id = formatted_msg.get("tool_call_id")
            if tool_call_id:
                if isinstance(content, str):
                    response_content = content
                elif content is None:
                    response_content = ""
                else:
                    response_content = str(content)

                parts.append(
                    {
                        "type": "tool_call_response",
                        "id": tool_call_id,
                        "response": response_content,
                    },
                )
                formatted_result = {
                    "role": role,
                    "parts": parts,
                }
                if "name" in formatted_msg:
                    formatted_result["name"] = formatted_msg["name"]
                return formatted_result

        # Handle list content/parts
        if isinstance(content, list) and len(content) > 0:
            for block in content:
                if isinstance(block, dict):
                    parts.extend(_parse_content_block(block))

        # Handle tool_calls (OpenAI/DashScope/DeepSeek/Ollama)
        # Note: Anthropic/Gemini put tool_use in content/parts, not tool_calls
        tool_calls = formatted_msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                func_info = tool_call.get("function", {})
                arguments = func_info.get("arguments", {})

                # OpenAI/DashScope/DeepSeek: arguments is JSON string
                # Ollama: arguments is dict
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except (json.JSONDecodeError, TypeError):
                        pass
                elif not isinstance(arguments, dict):
                    arguments = {}

                parts.append(
                    {
                        "type": "tool_call",
                        "id": tool_call.get("id", ""),
                        "name": func_info.get("name", ""),
                        "arguments": arguments,
                    },
                )

        # Handle Ollama images field (separate from content)
        images = formatted_msg.get("images")
        if isinstance(images, list):
            for image_data in images:
                if isinstance(image_data, str):
                    parts.append(
                        {
                            "type": "blob",
                            "content": image_data,
                            "media_type": "image/png",
                            "modality": "image",
                        },
                    )
        # Handle string content (OpenAI/DashScope/DeepSeek/Ollama)
        elif isinstance(content, str) and role != "tool":
            parts.append(
                {
                    "type": "text",
                    "content": content,
                },
            )

        formatted_result = {
            "role": role,
            "parts": parts,
        }

        # Preserve name if present
        if "name" in formatted_msg:
            formatted_result["name"] = formatted_msg["name"]

        return formatted_result

    except Exception:
        # Fallback to simple text part
        return {
            "role": formatted_msg.get("role", "user"),
            "parts": [
                {
                    "type": "text",
                    "content": str(formatted_msg.get("content", "")),
                },
            ],
        }
