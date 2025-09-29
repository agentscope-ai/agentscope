# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches
"""The Ollama formatter module."""
import base64
import os
from typing import Any
from urllib.parse import urlparse

from ._truncated_formatter_base import TruncatedFormatterBase
from .._logging import logger
from .._utils._common import _get_bytes_from_web_url
from ..message import Msg, TextBlock, ImageBlock, ToolUseBlock, ToolResultBlock
from ..token import TokenCounterBase


def _convert_ollama_image_url_to_base64_data(url: str) -> str:
    """Convert image url to base64."""
    parsed_url = urlparse(url)

    if not os.path.exists(url) and parsed_url.scheme != "":
        # Web url
        data = _get_bytes_from_web_url(url)
        return data
    if os.path.exists(url):
        # Local file
        with open(url, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")

        return data

    raise ValueError(
        f"The URL `{url}` is not a valid image URL or local file.",
    )


class OllamaChatFormatter(TruncatedFormatterBase):
    """Formatter for Ollama messages."""

    support_tools_api: bool = True
    """Whether support tools API"""

    support_multiagent: bool = False
    """Whether support multi-agent conversations"""

    support_vision: bool = True
    """Whether support vision data"""

    supported_blocks: list[type] = [
        TextBlock,
        # Multimodal
        ImageBlock,
        # Tool use
        ToolUseBlock,
        ToolResultBlock,
    ]
    """The list of supported message blocks"""

    async def _format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into Ollama API format.

        Args:
            msgs (`list[Msg]`):
                The list of message objects to format.

        Returns:
            `list[dict[str, Any]]`:
                The formatted messages as a list of dictionaries.
        """
        self.assert_list_of_msgs(msgs)

        messages: list[dict] = []
        for msg in msgs:
            content_blocks: list = []
            tool_calls = []
            images = []

            for block in msg.get_content_blocks():
                typ = block.get("type")
                if typ == "text":
                    content_blocks.append({**block})

                elif typ == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id"),
                            "type": "function",
                            "function": {
                                "name": block.get("name"),
                                "arguments": block.get("input", {}),
                            },
                        },
                    )

                elif typ == "tool_result":
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("id"),
                            "content": self.convert_tool_result_to_string(
                                block.get("output"),  # type: ignore[arg-type]
                            ),
                            "name": block.get("name"),
                        },
                    )

                elif typ == "image":
                    source_type = block["source"]["type"]
                    if source_type == "url":
                        images.append(
                            _convert_ollama_image_url_to_base64_data(
                                block["source"]["url"],
                            ),
                        )
                    elif source_type == "base64":
                        images.append(block["source"]["data"])

                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        typ,
                    )
            content_msg = "\n".join(
                content.get("text", "") for content in content_blocks
            )
            msg_ollama = {
                "role": msg.role,
                "content": content_msg or None,
            }

            if tool_calls:
                msg_ollama["tool_calls"] = tool_calls

            if images:
                msg_ollama["images"] = images

            if msg_ollama["content"] or msg_ollama.get("tool_calls"):
                messages.append(msg_ollama)

        return messages


class OllamaMultiAgentFormatter(TruncatedFormatterBase):
    """
    Ollama formatter for multi-agent conversations, where more than
    a user and an agent are involved.
    """

    support_tools_api: bool = True
    """Whether support tools API"""

    support_multiagent: bool = True
    """Whether support multi-agent conversations"""

    support_vision: bool = True
    """Whether support vision data"""

    supported_blocks: list[type] = [
        TextBlock,
        # Multimodal
        ImageBlock,
        # Tool use
        ToolUseBlock,
        ToolResultBlock,
    ]
    """The list of supported message blocks"""

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
        """Initialize the Ollama multi-agent formatter.

        Args:
            conversation_history_prompt (`str`):
                The prompt to use for the conversation history section.
            token_counter (`TokenCounterBase | None`, optional):
                The token counter used for truncation.
            max_tokens (`int | None`, optional):
                The maximum number of tokens allowed in the formatted
                messages. If `None`, no truncation will be applied.
        """
        super().__init__(token_counter=token_counter, max_tokens=max_tokens)
        self.conversation_history_prompt = conversation_history_prompt

    async def _format_system_message(
        self,
        msg: Msg,
    ) -> dict[str, Any]:
        """Format system message for the Ollama API."""
        return {
            "role": "system",
            "content": msg.get_text_content(),
        }

    async def _format_tool_sequence(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Given a sequence of tool call/result messages, format them into
        the required format for the Ollama API.

        Args:
            msgs (`list[Msg]`):
                The list of messages containing tool calls/results to format.

        Returns:
            `list[dict[str, Any]]`:
                A list of dictionaries formatted for the Ollama API.
        """
        return await OllamaChatFormatter().format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Given a sequence of messages without tool calls/results, format
        them into the required format for the Ollama API.

        Args:
            msgs (`list[Msg]`):
                A list of Msg objects to be formatted.
            is_first (`bool`, defaults to `True`):
                Whether this is the first agent message in the conversation.
                If `True`, the conversation history prompt will be included.

        Returns:
            `list[dict[str, Any]]`:
                A list of dictionaries formatted for the ollama API.
        """

        if is_first:
            conversation_history_prompt = self.conversation_history_prompt
        else:
            conversation_history_prompt = ""

        # Format into required Ollama format
        formatted_msgs: list[dict] = []

        # Collect the multimodal files
        conversation_blocks: list = []
        accumulated_text = []
        images = []
        for msg in msgs:
            for block in msg.get_content_blocks():
                if block["type"] == "text":
                    accumulated_text.append(f"{msg.name}: {block['text']}")

                elif block["type"] == "image":
                    # Handle the accumulated text as a single block
                    source = block["source"]
                    if accumulated_text:
                        conversation_blocks.append(
                            {"text": "\n".join(accumulated_text)},
                        )
                        accumulated_text.clear()

                    if source["type"] == "url":
                        images.append(
                            _convert_ollama_image_url_to_base64_data(
                                source["url"],
                            ),
                        )

                    elif source["type"] == "base64":
                        images.append(source["data"])

                    conversation_blocks.append({**block})

        if accumulated_text:
            conversation_blocks.append(
                {"text": "\n".join(accumulated_text)},
            )

        if conversation_blocks:
            if conversation_blocks[0].get("text"):
                conversation_blocks[0]["text"] = (
                    conversation_history_prompt
                    + "<history>\n"
                    + conversation_blocks[0]["text"]
                )

            else:
                conversation_blocks.insert(
                    0,
                    {
                        "text": conversation_history_prompt + "<history>\n",
                    },
                )

            if conversation_blocks[-1].get("text"):
                conversation_blocks[-1]["text"] += "\n</history>"

            else:
                conversation_blocks.append({"text": "</history>"})

        conversation_blocks_text = "\n".join(
            conversation_block.get("text", "")
            for conversation_block in conversation_blocks
        )

        user_message = {
            "role": "user",
            "content": conversation_blocks_text,
        }
        if images:
            user_message["images"] = images
        if conversation_blocks:
            formatted_msgs.append(user_message)

        return formatted_msgs
