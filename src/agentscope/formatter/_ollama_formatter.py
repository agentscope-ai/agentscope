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
from ..message import (
    Msg,
    TextBlock,
    ImageBlock,
    ToolUseBlock,
    ToolResultBlock,
    URLSource,
)
from ..token import TokenCounterBase


def _format_ollama_image_block(
    image_block: ImageBlock,
) -> str:
    """Format an image block for Ollama API.

    Args:
        image_block (`ImageBlock`):
            The image block to format.

    Returns:
        `str`:
            Base64 encoded image data as a string.

    Raises:
        `ValueError`:
            If the source type is not supported.
    """
    source = image_block["source"]
    if source["type"] == "url":
        return _convert_ollama_image_url_to_base64_data(source["url"])
    elif source["type"] == "base64":
        return source["data"]
    else:
        raise ValueError(
            f"Unsupported image source type: {source['type']}",
        )


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
    """The Ollama formatter class for chatbot scenario, where only a user
    and an agent are involved. We use the `role` field to identify different
    participants in the conversation.
    """

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

    def __init__(
        self,
        promote_tool_result_images: bool = False,
        token_counter: TokenCounterBase | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Initialize the Ollama chat formatter.

        Args:
            promote_tool_result_images (`bool`, defaults to `False`):
                Whether to promote images from tool results to user messages.
                Most LLM APIs don't support images in tool result blocks, but
                do support them in user message blocks. When `True`, images are
                extracted and appended as a separate user message with
                explanatory text indicating their source.
            token_counter (`TokenCounterBase | None`, optional):
                A token counter instance used to count tokens in the messages.
                If not provided, the formatter will format the messages
                without considering token limits.
            max_tokens (`int | None`, optional):
                The maximum number of tokens allowed in the formatted
                messages. If not provided, the formatter will not truncate
                the messages.
        """
        super().__init__(token_counter, max_tokens)
        self.promote_tool_result_images = promote_tool_result_images

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

        messages: list = []
        i = 0
        while i < len(msgs):
            msg = msgs[i]
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
                    (
                        textual_output,
                        multimodal_data,
                    ) = self.convert_tool_result_to_string(block["output"])

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("id"),
                            "content": textual_output,
                            "name": block.get("name"),
                        },
                    )

                    # Then, handle the multimodal data if any
                    promoted_blocks: list = []
                    for url, multimodal_block in multimodal_data:
                        if (
                            multimodal_block["type"] == "image"
                            and self.promote_tool_result_images
                        ):
                            promoted_blocks.extend(
                                [
                                    TextBlock(
                                        type="text",
                                        text=f"\n- The image from '{url}': ",
                                    ),
                                    ImageBlock(
                                        type="image",
                                        source=URLSource(
                                            type="url",
                                            url=url,
                                        ),
                                    ),
                                ],
                            )

                    if promoted_blocks:
                        # Insert promoted blocks as new user message(s)
                        promoted_blocks = [
                            TextBlock(
                                type="text",
                                text="<system-info>The following are "
                                "the image contents from the tool "
                                f"result of '{block['name']}':",
                            ),
                            *promoted_blocks,
                            TextBlock(
                                type="text",
                                text="</system-info>",
                            ),
                        ]

                        msgs.insert(
                            i + 1,
                            Msg(
                                name="user",
                                content=promoted_blocks,
                                role="user",
                            ),
                        )

                elif typ == "image":
                    images.append(
                        _format_ollama_image_block(
                            block,  # type: ignore[arg-type]
                        ),
                    )

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

            if (
                msg_ollama["content"]
                or msg_ollama.get("images")
                or msg_ollama.get("tool_calls")
            ):
                messages.append(msg_ollama)

            # Move to next message
            i += 1

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
        promote_tool_result_images: bool = False,
        token_counter: TokenCounterBase | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Initialize the Ollama multi-agent formatter.

        Args:
            conversation_history_prompt (`str`):
                The prompt to use for the conversation history section.
            promote_tool_result_images (`bool`, defaults to `False`):
                Whether to promote images from tool results to user messages.
                Most LLM APIs don't support images in tool result blocks, but
                do support them in user message blocks. When `True`, images are
                extracted and appended as a separate user message with
                explanatory text indicating their source.
            token_counter (`TokenCounterBase | None`, optional):
                The token counter used for truncation.
            max_tokens (`int | None`, optional):
                The maximum number of tokens allowed in the formatted
                messages. If `None`, no truncation will be applied.
        """
        super().__init__(token_counter=token_counter, max_tokens=max_tokens)
        self.conversation_history_prompt = conversation_history_prompt
        self.promote_tool_result_images = promote_tool_result_images

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
        return await OllamaChatFormatter(
            promote_tool_result_images=self.promote_tool_result_images,
        ).format(msgs)

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
                    if accumulated_text:
                        conversation_blocks.append(
                            {"text": "\n".join(accumulated_text)},
                        )
                        accumulated_text.clear()

                    images.append(_format_ollama_image_block(block))
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
