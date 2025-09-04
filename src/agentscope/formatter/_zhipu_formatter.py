# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches
"""The Zhipu AI formatter for agentscope."""
import base64
import json
import os
from typing import Any
from urllib.parse import urlparse

from ._truncated_formatter_base import TruncatedFormatterBase
from .._logging import logger
from ..message import (
    Msg,
    TextBlock,
    ImageBlock,
    ToolUseBlock,
    ToolResultBlock,
    ThinkingBlock,
)


def _to_zhipu_image_url(url: str) -> str:
    """Convert an image url to Zhipu AI format. If the given url is a local
    file, it will be converted to base64 format. Otherwise, it will be
    returned directly.

    Args:
        url (`str`):
            The local or public url of the image.
    """
    support_image_extensions = (
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
    )

    parsed_url = urlparse(url)
    lower_url = url.lower()

    if not os.path.exists(url) and parsed_url.scheme != "":
        if any(lower_url.endswith(_) for _ in support_image_extensions):
            return url

    elif os.path.exists(url) and os.path.isfile(url):
        if any(lower_url.endswith(_) for _ in support_image_extensions):
            with open(url, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode(
                    "utf-8",
                )
            extension = parsed_url.path.lower().split(".")[-1]
            mime_type = f"image/{extension}"
            return f"data:{mime_type};base64,{base64_image}"

    raise TypeError(f'"{url}" should end with {support_image_extensions}.')


class ZhipuChatFormatter(TruncatedFormatterBase):
    """The class used to format message objects into the Zhipu AI API required
    format."""

    support_tools_api: bool = True
    """Whether support tools API"""

    support_multiagent: bool = True
    """Whether support multi-agent conversation"""

    support_vision: bool = True
    """Whether support vision models"""

    supported_blocks: list[type] = [
        TextBlock,
        ImageBlock,
        ToolUseBlock,
        ToolResultBlock,
        ThinkingBlock,
    ]
    """Supported message blocks for Zhipu AI API"""

    async def _format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into Zhipu AI API required format.

        Args:
            msgs (`list[Msg]`):
                The list of Msg objects to format.

        Returns:
            `list[dict[str, Any]]`:
                A list of dictionaries, where each dictionary has "role"
                and "content" keys.
        """
        self.assert_list_of_msgs(msgs)

        messages: list[dict] = []
        for msg in msgs:
            content_blocks = []
            tool_calls = []

            for block in msg.get_content_blocks():
                typ = block.get("type")
                if typ == "text":
                    content_blocks.append({**block})

                elif typ == "thinking":
                    content_blocks.append(
                        {
                            "type": "text",
                            "text": f"[Thinking] {block.get('thinking', '')}",
                        }
                    )

                elif typ == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id"),
                            "type": "function",
                            "function": {
                                "name": block.get("name"),
                                "arguments": json.dumps(
                                    block.get("input", {}),
                                    ensure_ascii=False,
                                ),
                            },
                        },
                    )

                elif typ == "tool_result":
                    content_blocks.append(
                        {
                            "type": "text",
                            "text": self.convert_tool_result_to_string(
                                block.get("output"),  # type: ignore[arg-type]
                            ),
                        }
                    )

                elif typ == "image":
                    source_type = block["source"]["type"]
                    if source_type == "url":
                        url = _to_zhipu_image_url(block["source"]["url"])
                        content_blocks.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": url},
                            },
                        )
                    elif source_type == "base64":
                        data = block["source"]["data"]
                        media_type = block["source"]["media_type"]
                        url = f"data:{media_type};base64,{data}"
                        content_blocks.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": url},
                            },
                        )

                else:
                    logger.warning(
                        f"Unsupported message block type: {typ} in "
                        f"ZhipuChatFormatter. Skipping this block.",
                    )

            message = {
                "role": msg.role,
            }

            if (
                len(content_blocks) == 1
                and content_blocks[0]["type"] == "text"
            ):
                message["content"] = content_blocks[0]["text"]
            elif content_blocks:
                message["content"] = content_blocks

            if tool_calls:
                message["tool_calls"] = tool_calls

            if "content" in message or tool_calls:
                messages.append(message)

        return messages


class ZhipuMultiAgentFormatter(ZhipuChatFormatter):
    """The class used to format message objects into the Zhipu AI API required
    format for multi-agent conversation."""

    async def _format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into Zhipu AI API required format for
        multi-agent conversation.

        Args:
            msgs (`list[Msg]`):
                The list of Msg objects to format.

        Returns:
            `list[dict[str, Any]]`:
                A list of dictionaries, where each dictionary has "role",
                "content", and optionally "name" keys.
        """
        messages = await super()._format(msgs)

        for i, (msg, message) in enumerate(zip(msgs, messages)):
            if msg.name and msg.name != msg.role:
                message["name"] = msg.name

        return messages
