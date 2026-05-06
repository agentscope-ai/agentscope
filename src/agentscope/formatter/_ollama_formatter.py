# -*- coding: utf-8 -*-
"""The Ollama formatter module."""
import base64
import fnmatch
from abc import ABC
from typing import Any

import requests
from pydantic import Field

from ._formatter_base import FormatterBase
from .._logging import logger
from ..message import (
    Msg,
    TextBlock,
    HintBlock,
    DataBlock,
    ToolCallBlock,
    ToolResultBlock,
    URLSource,
    Base64Source,
)


class _OllamaFormatterBase(FormatterBase, ABC):
    """Base class for Ollama formatters, providing shared data block
    formatting logic."""

    supported_input_media_types: list[str]

    def _format_ollama_data_block(
        self,
        block: DataBlock,
    ) -> str | None:
        """Format a DataBlock into Ollama API format (base64 string).

        Args:
            block (`DataBlock`):
                The data block to format.

        Returns:
            `str | None`:
                Base64 encoded data as a string, or None if the media type
                is not supported.
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

        # Ollama only supports images
        if not media_type.startswith("image/"):
            logger.warning(
                "Ollama only supports image data, got %s, skipped.",
                media_type,
            )
            return None

        return self._format_image_source(source)

    @staticmethod
    def _format_image_source(source: URLSource | Base64Source) -> str:
        """Format an image source into Ollama API format (base64 string).

        Args:
            source (`URLSource | Base64Source`):
                The image source to format.

        Returns:
            `str`:
                Base64 encoded image data.
        """
        if isinstance(source, Base64Source):
            return source.data
        elif isinstance(source, URLSource):
            url = source.url
            if url.startswith("file://"):
                # Local file - read and convert to base64
                file_path = url.removeprefix("file://")
                with open(file_path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("utf-8")
                return data
            else:
                # Remote URL - download and convert to base64
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                data = base64.b64encode(response.content).decode("utf-8")
                return data
        else:
            raise ValueError(f"Unsupported source type: {type(source)}")


class OllamaChatFormatter(_OllamaFormatterBase):
    """The Ollama formatter class for chatbot scenario, where only a user
    and an agent are involved. We use the `role` field to identify different
    participants in the conversation.
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
            content_parts = []
            images = []

            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    content_parts.append(block.text)

                elif isinstance(block, HintBlock):
                    pass  # Ollama does not support hint blocks

                elif isinstance(block, DataBlock):
                    formatted_image = self._format_ollama_data_block(block)
                    if formatted_image:
                        images.append(formatted_image)

                elif isinstance(block, ToolCallBlock):
                    messages.append(
                        {
                            "role": msg.role,
                            "content": "\n".join(content_parts)
                            if content_parts
                            else "",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": block.name,
                                        "arguments": block.input,
                                    },
                                },
                            ],
                        },
                    )
                    content_parts = []
                    images = []

                elif isinstance(block, ToolResultBlock):
                    (
                        textual_output,
                        multimodal_data,
                    ) = self.convert_tool_result_to_string(block.output)

                    # If there's multimodal data, insert a user message
                    if multimodal_data:
                        user_images = []
                        user_content_parts = []
                        for data_block in multimodal_data:
                            if isinstance(data_block, DataBlock):
                                formatted_image = (
                                    self._format_ollama_data_block(
                                        data_block,
                                    )
                                )
                                if formatted_image:
                                    user_images.append(formatted_image)
                            elif isinstance(data_block, TextBlock):
                                user_content_parts.append(data_block.text)

                        user_msg = {
                            "role": "user",
                            "content": "\n".join(user_content_parts)
                            if user_content_parts
                            else textual_output,
                        }
                        if user_images:
                            user_msg["images"] = user_images
                        messages.append(user_msg)
                    else:
                        # No multimodal data, just add text to content
                        content_parts.append(textual_output)

                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        type(block),
                    )

            # Add the message if there's content or images
            if content_parts or images:
                msg_ollama: dict[str, Any] = {
                    "role": msg.role,
                    "content": "\n".join(content_parts)
                    if content_parts
                    else "",
                }
                if images:
                    msg_ollama["images"] = images
                messages.append(msg_ollama)

        return messages


class OllamaMultiAgentFormatter(_OllamaFormatterBase):
    """
    Ollama formatter for multi-agent conversations, where more than
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
        """Format input messages into the structure required by the Ollama
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
                        await self._format_tool_sequence(group),
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

    async def _format_tool_sequence(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format a sequence of tool-related messages."""
        return await OllamaChatFormatter(
            supported_input_media_types=self.supported_input_media_types,
        ).format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool,
    ) -> list[dict[str, Any]]:
        """Format agent messages into conversation history format."""
        conversation_blocks: list[dict] = []
        accumulated_text: list[str] = []
        images: list[str] = []

        for msg in msgs:
            msg_text_parts = []
            if msg.name:
                msg_text_parts.append(f"{msg.name}:")

            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    msg_text_parts.append(block.text)
                elif isinstance(block, DataBlock):
                    formatted_image = self._format_ollama_data_block(block)
                    if formatted_image:
                        images.append(formatted_image)
                elif isinstance(block, HintBlock):
                    pass  # Ollama does not support hint blocks
                else:
                    logger.warning(
                        "Unsupported block type %s in agent message, skipped.",
                        type(block),
                    )

            if msg_text_parts:
                accumulated_text.append("\n".join(msg_text_parts))

        if accumulated_text:
            conversation_blocks.append(
                {"text": "\n".join(accumulated_text)},
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
                        "text": self.conversation_history_prompt
                        + "<history>\n",
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

        user_message: dict[str, Any] = {
            "role": "user",
            "content": conversation_blocks_text,
        }
        if images:
            user_message["images"] = images

        formatted_msgs = []
        if conversation_blocks:
            formatted_msgs.append(user_message)

        return formatted_msgs

    @staticmethod
    async def _format_system_message(msg: Msg) -> dict[str, Any]:
        """Format a system message."""
        text_parts = []
        for block in msg.get_content_blocks():
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            else:
                logger.warning(
                    "Unsupported block type %s in system message, skipped.",
                    type(block),
                )
        return {
            "role": "system",
            "content": "\n".join(text_parts),
        }
