# -*- coding: utf-8 -*-
"""Google Gemini API formatter in agentscope."""
import base64
import fnmatch
from abc import ABC
from typing import Any

import requests

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
    UserMsg,
    URLSource,
    Base64Source,
)


class _GeminiFormatterBase(FormatterBase, ABC):
    """Base class for Gemini formatters, providing shared data block
    formatting logic."""

    supported_input_media_types: list[str]

    def _format_gemini_data_block(
        self,
        block: DataBlock,
    ) -> dict[str, Any] | None:
        """Format a DataBlock into Gemini API format.

        Args:
            block (`DataBlock`):
                The data block to format.

        Returns:
            `dict[str, Any] | None`:
                The formatted data block in Gemini ``inline_data`` format,
                or None if the media type is not supported.
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

        return self._format_media_source(source)

    @staticmethod
    def _format_media_source(
        source: URLSource | Base64Source,
    ) -> dict[str, Any]:
        """Format a media source into Gemini API ``inline_data`` format.

        Args:
            source (`URLSource | Base64Source`):
                The media source to format.

        Returns:
            `dict[str, Any]`:
                The formatted media source.
        """
        if isinstance(source, Base64Source):
            return {
                "inline_data": {
                    "data": source.data,
                    "mime_type": source.media_type,
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
                    "inline_data": {
                        "data": data,
                        "mime_type": source.media_type,
                    },
                }
            else:
                # Remote URL - download and convert to base64
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                data = base64.b64encode(response.content).decode("utf-8")
                return {
                    "inline_data": {
                        "data": data,
                        "mime_type": source.media_type,
                    },
                }
        else:
            raise ValueError(f"Unsupported source type: {type(source)}")


class GeminiChatFormatter(_GeminiFormatterBase):
    """The Gemini formatter class for chatbot scenario, where only a user
    and an agent are involved. We use the `role` field to identify different
    entities in the conversation.
    """

    def __init__(
        self,
        supported_input_media_types: list[str] | None = None,
    ) -> None:
        super().__init__(
            supported_input_media_types=supported_input_media_types
            or ["image/*", "audio/*", "video/*"],
        )

    async def format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into Gemini API required format.

        Args:
            msgs (`list[Msg]`):
                The list of message objects to format.

        Returns:
            `list[dict[str, Any]]`:
                The formatted messages as a list of dictionaries.
        """
        self.assert_list_of_msgs(msgs)

        messages: list[dict] = []
        i = 0
        while i < len(msgs):
            msg = msgs[i]
            parts: list = []

            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    parts.append({"text": block.text})

                elif isinstance(block, ThinkingBlock):
                    parts.append({"text": block.thinking})

                elif isinstance(block, HintBlock):
                    pass  # Gemini does not support hint blocks

                elif isinstance(block, DataBlock):
                    formatted = self._format_gemini_data_block(block)
                    if formatted:
                        parts.append(formatted)

                elif isinstance(block, ToolCallBlock):
                    parts.append(
                        {
                            "function_call": {
                                "id": None,
                                "name": block.name,
                                "args": block.input,
                            },
                            "thought_signature": block.id,
                        },
                    )

                elif isinstance(block, ToolResultBlock):
                    (
                        textual_output,
                        multimodal_data,
                    ) = self.convert_tool_result_to_string(block.output)

                    messages.append(
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "function_response": {
                                        "id": block.id,
                                        "name": block.name,
                                        "response": {
                                            "output": textual_output,
                                        },
                                    },
                                },
                            ],
                        },
                    )

                    if multimodal_data:
                        msgs.insert(
                            i + 1,
                            UserMsg(
                                name="system-reminder",
                                content=multimodal_data,
                            ),
                        )

                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        type(block),
                    )

            # Gemini uses "model" instead of "assistant"
            role = "model" if msg.role == "assistant" else "user"

            if parts:
                messages.append(
                    {
                        "role": role,
                        "parts": parts,
                    },
                )

            i += 1

        return messages


class GeminiMultiAgentFormatter(_GeminiFormatterBase):
    """The multi-agent formatter for Google Gemini API, where more than a
    user and an agent are involved.

    .. note:: This formatter will combine previous messages (except tool
     calls/results) into a history section in the first system message with
     the conversation history prompt.

    .. note:: For tool calls/results, they will be presented as separate
     messages as required by the Gemini API. Therefore, the tool calls/
     results messages are expected to be placed at the end of the input
     messages.

    .. tip:: Telling the assistant's name in the system prompt is very
     important in multi-agent conversations. So that LLM can know who it
     is playing as.

    """

    def __init__(
        self,
        conversation_history_prompt: str = (
            "# Conversation History\n"
            "The content between <history></history> tags contains "
            "your conversation history\n"
        ),
        supported_input_media_types: list[str] | None = None,
    ) -> None:
        """Initialize the Gemini multi-agent formatter.

        Args:
            conversation_history_prompt (`str`):
                The prompt to be used for the conversation history section.
            supported_input_media_types (`list[str] | None`, optional):
                The list of supported input media types. Defaults to
                ``["image/*", "audio/*", "video/*"]``.
        """
        super().__init__(
            supported_input_media_types=supported_input_media_types
            or ["image/*", "audio/*", "video/*"],
        )
        self.conversation_history_prompt = conversation_history_prompt

    async def format(self, msgs: list[Msg]) -> list[dict[str, Any]]:
        """Format input messages into the structure required by the Gemini
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
        """Given a sequence of tool call/result messages, format them into
        the required format for the Gemini API."""
        return await GeminiChatFormatter(
            supported_input_media_types=self.supported_input_media_types,
        ).format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Given a sequence of messages without tool calls/results, format
        them into the required format for the Gemini API."""

        if is_first:
            conversation_history_prompt = self.conversation_history_prompt
        else:
            conversation_history_prompt = ""

        formatted_msgs: list[dict] = []
        conversation_parts: list[dict] = []
        accumulated_text: list[str] = []

        for msg in msgs:
            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    accumulated_text.append(f"{msg.name}: {block.text}")

                elif isinstance(block, DataBlock):
                    # Flush accumulated text first
                    if accumulated_text:
                        conversation_parts.append(
                            {"text": "\n".join(accumulated_text)},
                        )
                        accumulated_text = []

                    formatted = self._format_gemini_data_block(block)
                    if formatted:
                        conversation_parts.append(formatted)

        if accumulated_text:
            conversation_parts.append(
                {"text": "\n".join(accumulated_text)},
            )

        # Add prompt and <history></history> tags around conversation history
        if conversation_parts:
            if conversation_parts[0].get("text"):
                conversation_parts[0]["text"] = (
                    conversation_history_prompt
                    + "<history>\n"
                    + conversation_parts[0]["text"]
                )
            else:
                conversation_parts.insert(
                    0,
                    {"text": conversation_history_prompt + "<history>\n"},
                )

            if conversation_parts[-1].get("text"):
                conversation_parts[-1]["text"] += "\n</history>"
            else:
                conversation_parts.append({"text": "</history>"})

            formatted_msgs.append(
                {
                    "role": "user",
                    "parts": conversation_parts,
                },
            )

        return formatted_msgs

    @staticmethod
    async def _format_system_message(msg: Msg) -> dict[str, Any]:
        """Format system message for the Gemini API."""
        return {
            "role": "user",
            "parts": [
                {
                    "text": msg.get_text_content(),
                },
            ],
        }
