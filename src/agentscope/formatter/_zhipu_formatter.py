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
    VideoBlock,
)
from ..token import TokenCounterBase


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
            # 根据评审意见，修正Base64格式处理
            # 使用url而不是parsed_url.path来提取扩展名，以处理本地文件路径
            extension = url.lower().split(".")[-1]
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
        VideoBlock,
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
                    # 根据评审意见和AgentScope规范，除了Anthropic外的其他API不应包含thinking内容
                    # Zhipu API不应在发送给模型的消息中包含thinking内容
                    logger.warning(
                        "Thinking content is not recommended for Zhipu AI API. "
                        "Skipping thinking block.",
                    )
                    # 不添加thinking内容到消息中
                    continue

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
                        },
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

                elif typ == "video":
                    # 根据评审意见，添加对VideoBlock的支持
                    source_type = block["source"]["type"]
                    if source_type == "url":
                        content_blocks.append(
                            {
                                "type": "video_url",
                                "video_url": {"url": block["source"]["url"]},
                            },
                        )
                    elif source_type == "base64":
                        data = block["source"]["data"]
                        media_type = block["source"]["media_type"]
                        url = f"data:{media_type};base64,{data}"
                        content_blocks.append(
                            {
                                "type": "video_url",
                                "video_url": {"url": url},
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

    support_multiagent: bool = True
    """Whether support multi-agent conversation"""

    def __init__(
        self,
        conversation_history_prompt: str = (
            "# Conversation History\n"
            "The content between <history></history> tags contains "
            "your conversation history\n"
        ),
        token_counter: TokenCounterBase | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Initialize the ZhipuAI multi-agent formatter.

        Args:
            conversation_history_prompt (`str`):
                The prompt to use for the conversation history section.
        """
        super().__init__(token_counter=token_counter, max_tokens=max_tokens)
        self.conversation_history_prompt = conversation_history_prompt

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
                A list of dictionaries, properly formatted for multi-agent
                conversation.
        """
        # Check if this is a simple case (no tool calls)
        has_tool_calls = False
        for msg in msgs:
            for block in msg.get_content_blocks():
                if block["type"] in ["tool_use", "tool_result"]:
                    has_tool_calls = True
                    break

        # For simple cases (no tool calls), use the parent formatting and add name fields
        if not has_tool_calls:
            formatted = await super()._format(msgs)
            # Add name fields to the formatted messages
            for i, msg in enumerate(msgs):
                if (
                    msg.name != msg.role
                ):  # Only add name if different from role
                    formatted[i]["name"] = msg.name
            return formatted

        # For complex multi-agent conversations, use the original logic
        formatted_msgs: list[dict] = []

        # Collect messages without tool calls/results
        conversation_blocks: list = []
        accumulated_text = []

        for msg in msgs:
            has_tool_content = False
            for block in msg.get_content_blocks():
                if block["type"] in ["tool_use", "tool_result"]:
                    has_tool_content = True
                    break

            if has_tool_content:
                # Process accumulated conversation text
                if accumulated_text:
                    conversation_text = "\n".join(accumulated_text)
                    if not conversation_blocks:  # First block
                        conversation_text = (
                            self.conversation_history_prompt
                            + "<history>\n"
                            + conversation_text
                        )

                    conversation_blocks.append({"text": conversation_text})
                    accumulated_text.clear()

                # Process tool messages separately
                tool_messages = await super()._format([msg])
                if conversation_blocks:
                    # Close the conversation history tag
                    conversation_blocks[-1]["text"] += "\n</history>"
                    formatted_msgs.append(
                        {
                            "role": "user",
                            "content": conversation_blocks,
                        },
                    )
                    conversation_blocks = []

                formatted_msgs.extend(tool_messages)
            else:
                # Accumulate conversation messages
                text_content = msg.get_text_content()
                if text_content:
                    accumulated_text.append(f"{msg.name}: {text_content}")

        # Handle remaining conversation messages
        if accumulated_text:
            conversation_text = "\n".join(accumulated_text)
            if not conversation_blocks:  # First block
                conversation_text = (
                    self.conversation_history_prompt
                    + "<history>\n"
                    + conversation_text
                )

            conversation_blocks.append({"text": conversation_text})

        if conversation_blocks:
            # Close the conversation history tag
            conversation_blocks[-1]["text"] += "\n</history>"
            formatted_msgs.append(
                {
                    "role": "user",
                    "content": conversation_blocks,
                },
            )

        return formatted_msgs
