# -*- coding: utf-8 -*-
"""The Anthropic formatter module."""
import base64
import fnmatch
import json
from abc import ABC
from typing import Any

import requests
from pydantic import Field

from ._formatter_base import FormatterBase
from .._logging import logger
from ..message import (
    Msg,
    TextBlock,
    ThinkingBlock,
    HintBlock,
    DataBlock,
    ToolCallBlock,
    ToolResultBlock,
    URLSource,
    Base64Source,
)


class _AnthropicFormatterBase(FormatterBase, ABC):
    """Mixin for formatting Anthropic formatters to avoid duplication between
    AnthropicChatFormatter and AnthropicMultiAgentFormatter."""

    supported_input_media_types: list[str]

    async def _format_messages(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into Anthropic API format.

        Args:
            msgs (`list[Msg]`):
                The list of message objects to format.

        Returns:
            `list[dict[str, Any]]`:
                The formatted messages as a list of dictionaries.

        .. note:: Anthropic suggests always passing all previous thinking
         blocks back to the API in subsequent calls to maintain reasoning
         continuity. For more details, please refer to
         `Anthropic's documentation
         <https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking#preserving-thinking-blocks>`_.
        """
        self.assert_list_of_msgs(msgs)

        messages: list[dict] = []
        for msg in msgs:
            content_blocks: list = []
            has_tool_result = False

            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    content_blocks.append(
                        {"type": "text", "text": block.text},
                    )

                elif isinstance(block, ThinkingBlock):
                    content_blocks.append(
                        {"type": "thinking", "thinking": block.thinking},
                    )

                elif isinstance(block, HintBlock):
                    pass  # Anthropic does not support hint blocks

                elif isinstance(block, DataBlock):
                    formatted_block = self._format_anthropic_data_block(block)
                    if formatted_block:
                        content_blocks.append(formatted_block)

                elif isinstance(block, ToolCallBlock):
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            # Anthropic API expects input as a dict, not a
                            # JSON string.
                            "input": json.loads(block.input or "{}"),
                        },
                    )

                elif isinstance(block, ToolResultBlock):
                    (
                        textual_output,
                        multimodal_data,
                    ) = self.convert_tool_result_to_string(block.output)

                    tool_result_content = []
                    if textual_output:
                        tool_result_content.append(
                            {"type": "text", "text": textual_output},
                        )

                    for data_block in multimodal_data:
                        formatted_block = self._format_anthropic_data_block(
                            data_block,
                        )
                        if formatted_block:
                            tool_result_content.append(formatted_block)

                    content_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": tool_result_content,
                        },
                    )
                    # Anthropic requires tool_result to be in a "user" message.
                    has_tool_result = True

                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        type(block),
                    )

            if content_blocks:
                # Anthropic requires `tool_result` blocks to be in a `user`
                # message regardless of the containing Msg's role.
                role = "user" if has_tool_result else msg.role
                messages.append(
                    {
                        "role": role,
                        "content": content_blocks,
                    },
                )

        return messages

    def _format_anthropic_data_block(
        self,
        block: DataBlock,
    ) -> dict[str, Any] | None:
        """Format a DataBlock into Anthropic API format.

        Args:
            block (`DataBlock`):
                The data block to format.

        Returns:
            `dict[str, Any] | None`:
                The formatted data block, or None if the media type is not
                supported.
        """
        source = block.source
        media_type = source.media_type

        # Check if media type is supported
        if not any(
            fnmatch.fnmatch(media_type, pattern)
            for pattern in self.supported_input_media_types
        ):
            logger.warning(
                "Media type %s is not supported, skipped.",
                media_type,
            )
            return None

        # Anthropic only supports images
        if not media_type.startswith("image/"):
            logger.warning(
                "Anthropic only supports image data, got %s, skipped.",
                media_type,
            )
            return None

        return self._format_image_source(source)

    @staticmethod
    def _format_image_source(
        source: URLSource | Base64Source,
    ) -> dict[str, Any]:
        """Format an image source into Anthropic API format.

        Args:
            source (`URLSource | Base64Source`):
                The image source to format.

        Returns:
            `dict[str, Any]`:
                The formatted image source.
        """
        if isinstance(source, Base64Source):
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": source.media_type,
                    "data": source.data,
                },
            }
        elif isinstance(source, URLSource):
            url = source.url
            if url.startswith("file://"):
                # Local file - read and convert to base64
                file_path = url.removeprefix("file://")
                with open(file_path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("utf-8")
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": source.media_type,
                        "data": data,
                    },
                }
            else:
                # Remote URL - download and convert to base64
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                data = base64.b64encode(response.content).decode("utf-8")
                return {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": source.media_type,
                        "data": data,
                    },
                }
        else:
            raise ValueError(f"Unsupported source type: {type(source)}")


class AnthropicChatFormatter(_AnthropicFormatterBase):
    """The Anthropic formatter class for chatbot scenario, where only a user
    and an agent are involved. We use the `role` field to identify different
    entities in the conversation.
    """

    supported_input_media_types: list[str] = Field(
        default_factory=lambda: ["image/*"],
        description=(
            'The supported input media types. Defaults to ``["image/*"]``.'
        ),
    )

    async def format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into Anthropic API format.

        Args:
            msgs (`list[Msg]`):
                The list of message objects to format.

        Returns:
            `list[dict[str, Any]]`:
                The formatted messages as a list of dictionaries.

        .. note:: Anthropic suggests always passing all previous thinking
         blocks back to the API in subsequent calls to maintain reasoning
         continuity. For more details, please refer to
         `Anthropic's documentation
         <https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking#preserving-thinking-blocks>`_.
        """
        return await self._format_messages(msgs)


class AnthropicMultiAgentFormatter(_AnthropicFormatterBase):
    """Anthropic formatter for multi-agent conversations, where more than
    a user and an agent are involved.
    """

    conversation_history_prompt: str = Field(
        default=(
            "# Conversation History\n"
            "The content between <history></history> tags contains "
            "your conversation history\n"
        ),
        description="The prompt to use for the conversation history section.",
    )

    supported_input_media_types: list[str] = Field(
        default_factory=lambda: ["image/*"],
        description=(
            'The supported input media types. Defaults to ``["image/*"]``.'
        ),
    )

    async def format(self, msgs: list[Msg]) -> list[dict[str, Any]]:
        """Format input messages into the structure required by the Anthropic
        API for multi-agent conversations."""
        self.assert_list_of_msgs(msgs)

        formatted_msgs = []
        start_index = 0
        if len(msgs) > 0 and msgs[0].role == "system":
            formatted_msgs.append(
                await self._format_system_message(msgs[0]),
            )
            start_index = 1

        is_first_agent_message = True
        async for typ, group in self._group_messages(msgs[start_index:]):
            match typ:
                case "tool_sequence":
                    formatted_msgs.extend(
                        await self._format_messages(group),
                    )
                case "agent_message":
                    formatted_msgs.extend(
                        await self._format_agent_message(
                            group,
                            is_first_agent_message,
                        ),
                    )
                    is_first_agent_message = False

        return formatted_msgs

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool,
    ) -> list[dict[str, Any]]:
        """Format agent messages into conversation history."""
        conversation_blocks = []
        accumulated_text = []

        for msg in msgs:
            agent_name = msg.name or "Agent"
            agent_text_parts = []

            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    agent_text_parts.append(block.text)
                elif isinstance(block, DataBlock):
                    formatted_block = self._format_anthropic_data_block(block)
                    if formatted_block:
                        if accumulated_text:
                            conversation_blocks.append(
                                {
                                    "type": "text",
                                    "text": "\n".join(accumulated_text),
                                },
                            )
                            accumulated_text = []
                        conversation_blocks.append(formatted_block)

            if agent_text_parts:
                agent_message = f"{agent_name}: {' '.join(agent_text_parts)}"
                accumulated_text.append(agent_message)

        if accumulated_text:
            conversation_blocks.append(
                {
                    "type": "text",
                    "text": "\n".join(accumulated_text),
                },
            )

        if conversation_blocks and is_first:
            if conversation_blocks[0].get("text"):
                conversation_blocks[0]["text"] = (
                    self.conversation_history_prompt
                    + "<history>\n"
                    + conversation_blocks[0]["text"]
                )
            else:
                conversation_blocks.insert(
                    0,
                    {
                        "type": "text",
                        "text": self.conversation_history_prompt
                        + "<history>\n",
                    },
                )

            if conversation_blocks[-1].get("text"):
                conversation_blocks[-1]["text"] += "\n</history>"
            else:
                conversation_blocks.append(
                    {"type": "text", "text": "</history>"},
                )

        if conversation_blocks:
            return [
                {
                    "role": "user",
                    "content": conversation_blocks,
                },
            ]

        return []

    @staticmethod
    async def _format_system_message(msg: Msg) -> dict[str, Any]:
        """Format a system message."""
        text_parts = []
        for block in msg.get_content_blocks():
            if isinstance(block, TextBlock):
                text_parts.append(block.text)

        return {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "\n".join(text_parts),
                },
            ],
        }
