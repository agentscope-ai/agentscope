# -*- coding: utf-8 -*-
"""The DashScope formatter module (OpenAI-compatible format)."""

import base64
from typing import Any
from fnmatch import fnmatch
from abc import ABC

from pydantic import Field

from ._formatter_base import FormatterBase
from .._logging import logger
from ..message import (
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    URLSource,
    DataBlock,
    ToolCallBlock,
    Base64Source,
    HintBlock,
)


class _DashScopeFormatterBase(FormatterBase, ABC):
    """Base class for DashScope formatters (OpenAI-compatible format),
    providing shared data block formatting logic."""

    input_types: list[str] = Field(
        default_factory=lambda: [
            "text/plain",
            "image/*",
            "audio/*",
            "video/*",
        ],
        description=(
            "The supported input types, aligned with the model card's "
            "``input_types`` field. Media types (non ``text/plain`` / "
            "``application/x-thinking`` entries) are used to filter "
            "``DataBlock``\\s; ``application/x-thinking`` enables passing "
            "``reasoning_content`` back to the API."
        ),
    )

    @property
    def supported_input_media_types(self) -> list[str]:
        """Derive supported media types from :attr:`input_types`, excluding
        ``text/plain`` and ``application/x-thinking``."""
        return [
            t
            for t in self.input_types
            if t not in ("text/plain", "application/x-thinking")
        ]

    @property
    def supports_thinking_input(self) -> bool:
        """Return ``True`` if ``application/x-thinking`` is listed in
        :attr:`input_types`, meaning the model accepts ``reasoning_content``
        in the conversation history."""
        return "application/x-thinking" in self.input_types

    def _format_dashscope_data_block(
        self,
        block: DataBlock,
    ) -> dict[str, Any] | None:
        """Format a DataBlock into the OpenAI-compatible format for
        DashScope API.

        Supports:
        - Images: ``{"type": "image_url", "image_url": {"url": ...}}``
        - Videos: ``{"type": "video_url", "video_url": {"url": ...}}``
        - Audio: ``{"type": "input_audio", "input_audio": {...}}``

        Args:
            block (`DataBlock`):
                The DataBlock to format.

        Returns:
            `dict[str, Any] | None`:
                A dictionary representing the formatted DataBlock, or ``None``
                if the media type is unsupported.
        """
        if not any(
            fnmatch(block.source.media_type, pattern)
            for pattern in self.supported_input_media_types
        ):
            logger.warning(
                "Unsupported media type %s for DashScope API. Supported "
                "types: %s. This block will be skipped.",
                block.source.media_type,
                ", ".join(self.supported_input_media_types),
            )
            return None

        main_type = block.source.media_type.split("/")[0]

        if main_type == "image":
            return self._format_image_source(block.source)

        if main_type == "video":
            return self._format_video_source(block.source)

        if main_type == "audio":
            return self._format_audio_source(block.source)

        logger.warning(
            "Unsupported main media type %s for DashScope API. "
            "This block will be skipped.",
            main_type,
        )
        return None

    @staticmethod
    def _format_image_source(
        source: URLSource | Base64Source,
    ) -> dict[str, Any]:
        """Convert an image source to OpenAI-compatible ``image_url`` format.

        Local ``file://`` URLs are read from disk and converted to base64
        data URIs. Remote URLs are passed through unchanged.
        """
        if isinstance(source, Base64Source):
            url = f"data:{source.media_type};base64,{source.data}"
        elif isinstance(source, URLSource):
            url_str = str(source.url)
            if url_str.startswith("file://"):
                local_path = url_str.removeprefix("file://")
                with open(local_path, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode("utf-8")
                url = f"data:{source.media_type};base64,{encoded}"
            else:
                url = url_str
        else:
            raise ValueError(f"Unsupported image source type: {type(source)}")

        return {
            "type": "image_url",
            "image_url": {"url": url},
        }

    @staticmethod
    def _format_video_source(
        source: URLSource | Base64Source,
    ) -> dict[str, Any]:
        """Convert a video source to DashScope's ``video_url`` format
        (OpenAI-compatible extension).

        Local ``file://`` URLs are read from disk and converted to base64
        data URIs. Remote URLs are passed through unchanged.
        """
        if isinstance(source, Base64Source):
            url = f"data:{source.media_type};base64,{source.data}"
        elif isinstance(source, URLSource):
            url_str = str(source.url)
            if url_str.startswith("file://"):
                local_path = url_str.removeprefix("file://")
                with open(local_path, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode("utf-8")
                url = f"data:{source.media_type};base64,{encoded}"
            else:
                url = url_str
        else:
            raise ValueError(f"Unsupported video source type: {type(source)}")

        return {
            "type": "video_url",
            "video_url": {"url": url},
        }

    @staticmethod
    def _format_audio_source(
        source: URLSource | Base64Source,
    ) -> dict[str, Any]:
        """Convert an audio source to DashScope ``input_audio`` format.

        DashScope's compatible API accepts URLs directly in the ``data``
        field (unlike standard OpenAI which requires base64). Local files
        are still read and base64-encoded.
        """
        if isinstance(source, Base64Source):
            fmt = source.media_type.split("/")[-1]
            return {
                "type": "input_audio",
                "input_audio": {
                    "data": source.data,
                    "format": fmt,
                },
            }

        if isinstance(source, URLSource):
            url_str = str(source.url)
            fmt = source.media_type.split("/")[-1]
            if url_str.startswith("file://"):
                local_path = url_str.removeprefix("file://")
                with open(local_path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("utf-8")
                return {
                    "type": "input_audio",
                    "input_audio": {
                        "data": data,
                        "format": fmt,
                    },
                }
            else:
                return {
                    "type": "input_audio",
                    "input_audio": {
                        "data": url_str,
                        "format": fmt,
                    },
                }

        raise ValueError(f"Unsupported audio source type: {type(source)}")


class DashScopeChatFormatter(_DashScopeFormatterBase):
    """The DashScope formatter class for chatbot scenario (OpenAI-compatible
    format), where only a user and an agent are involved. We use the ``role``
    field to identify different entities in the conversation.

    This formatter outputs messages in the OpenAI Chat Completions format,
    with DashScope-specific extensions for video (``video_url``) and
    thinking (``reasoning_content``).
    """

    # pylint: disable=too-many-branches
    async def format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into DashScope OpenAI-compatible format.

        Args:
            msgs (`list[Msg]`):
                The list of message objects to format.

        Returns:
            `list[dict[str, Any]]`:
                The formatted messages as a list of dictionaries.
        """
        self.assert_list_of_msgs(msgs)

        formatted_msgs: list[dict] = []
        i = 0
        while i < len(msgs):
            msg = msgs[i]
            content_blocks: list[dict] = []
            tool_calls = []
            thinking_parts: list[str] = []

            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    content_blocks.append({"type": "text", "text": block.text})

                elif isinstance(block, DataBlock):
                    formatted_block = self._format_dashscope_data_block(
                        block,
                    )
                    if formatted_block:
                        content_blocks.append(formatted_block)

                elif isinstance(block, HintBlock):
                    if content_blocks or tool_calls or thinking_parts:
                        msg_openai: dict[str, Any] = {
                            "role": msg.role,
                            "content": content_blocks or None,
                        }
                        if tool_calls:
                            msg_openai["tool_calls"] = tool_calls
                        if thinking_parts:
                            msg_openai["reasoning_content"] = "\n".join(
                                thinking_parts,
                            )
                        formatted_msgs.append(msg_openai)
                        content_blocks = []
                        tool_calls = []
                        thinking_parts = []

                    if isinstance(block.hint, str):
                        formatted_msgs.append(
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": block.hint},
                                ],
                            },
                        )
                    else:
                        hint_parts: list[dict] = []
                        for sub in block.hint:
                            if isinstance(sub, TextBlock):
                                hint_parts.append(
                                    {"type": "text", "text": sub.text},
                                )
                            elif isinstance(sub, DataBlock):
                                formatted_sub = (
                                    self._format_dashscope_data_block(
                                        sub,
                                    )
                                )
                                if formatted_sub:
                                    hint_parts.append(formatted_sub)
                        if hint_parts:
                            formatted_msgs.append(
                                {"role": "user", "content": hint_parts},
                            )

                elif isinstance(block, ToolCallBlock):
                    tool_calls.append(
                        {
                            "id": block.id,
                            "type": "function",
                            "function": {
                                "name": block.name,
                                "arguments": block.input,
                            },
                        },
                    )

                elif isinstance(block, ThinkingBlock):
                    if self.supports_thinking_input:
                        thinking_parts.append(block.thinking)

                elif isinstance(block, ToolResultBlock):
                    if content_blocks or tool_calls or thinking_parts:
                        msg_flush: dict[str, Any] = {
                            "role": msg.role,
                            "content": content_blocks or None,
                        }
                        if tool_calls:
                            msg_flush["tool_calls"] = tool_calls
                        if thinking_parts:
                            msg_flush["reasoning_content"] = "\n".join(
                                thinking_parts,
                            )
                        formatted_msgs.append(msg_flush)
                        content_blocks = []
                        tool_calls = []
                        thinking_parts = []

                    (
                        textual_output,
                        multimodal_data,
                    ) = self.convert_tool_result_to_string(block.output)

                    formatted_msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.id,
                            "content": textual_output,
                            "name": block.name,
                        },
                    )

                    if multimodal_data:
                        promo_content = []
                        for item in multimodal_data:
                            if isinstance(item, TextBlock):
                                promo_content.append(
                                    {"type": "text", "text": item.text},
                                )
                            elif isinstance(item, DataBlock):
                                fmt_item = self._format_dashscope_data_block(
                                    item,
                                )
                                if fmt_item is not None:
                                    promo_content.append(fmt_item)
                        if promo_content:
                            formatted_msgs.append(
                                {
                                    "role": "user",
                                    "content": promo_content,
                                },
                            )

                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        type(block),
                    )

            msg_dashscope: dict[str, Any] = {
                "role": msg.role,
                "content": content_blocks or None,
            }

            if tool_calls:
                msg_dashscope["tool_calls"] = tool_calls

            if thinking_parts:
                msg_dashscope["reasoning_content"] = "\n".join(thinking_parts)

            if (
                msg_dashscope["content"]
                or msg_dashscope.get("tool_calls")
                or msg_dashscope.get("reasoning_content")
            ):
                formatted_msgs.append(msg_dashscope)

            i += 1

        return formatted_msgs


class DashScopeMultiAgentFormatter(_DashScopeFormatterBase):
    """DashScope formatter for multi-agent conversations (OpenAI-compatible
    format), where more than a user and an agent are involved.

    .. note:: This formatter will combine previous messages (except tool
     calls/results) into a history section in the first system message with
     the conversation history prompt.

    .. note:: For tool calls/results, they will be presented as separate
     messages as required by the API. Therefore, the tool calls/results
     messages are expected to be placed at the end of the input messages.

    .. tip:: Telling the assistant's name in the system prompt is very
     important in multi-agent conversations. So that LLM can know who it
     is playing as.
    """

    conversation_history_prompt: str = Field(
        description="The conversation history prompt.",
        default=(
            "# Conversation History\n"
            "The content between <history></history> tags contains "
            "your conversation history\n"
        ),
    )

    async def format(self, msgs: list[Msg]) -> list[dict]:
        """Format input messages into the structure required by the DashScope
        OpenAI-compatible API.

        To support multi-agent conversations, this formatter processes messages
        as follows:

        - Prepends an instruction before the first conversation history
         section.
        - Combines conversation turns into a history section, where each entry
         is formatted as ``{name}: {content}``.
        - Wraps the conversation history with ``<history>`` and ``</history>``
         tags.

        Returns:
            `list[dict[str, Any]]`:
                A list of dictionaries formatted for the DashScope API.
        """

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
        the required format for the DashScope API."""
        return await DashScopeChatFormatter(
            input_types=self.input_types,
        ).format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Given a sequence of messages without tool calls/results, format
        them into a user message with conversation history tags."""
        if is_first:
            conversation_history_prompt = self.conversation_history_prompt
        else:
            conversation_history_prompt = ""

        formatted_msgs: list[dict] = []
        conversation_blocks: list = []
        accumulated_text = []
        media_blocks: list[dict] = []

        for msg in msgs:
            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    accumulated_text.append(f"{msg.name}: {block.text}")

                elif isinstance(block, DataBlock):
                    formatted_block = self._format_dashscope_data_block(
                        block,
                    )
                    if formatted_block is not None:
                        media_blocks.append(formatted_block)

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
                    {"text": conversation_history_prompt + "<history>\n"},
                )

            if conversation_blocks[-1].get("text"):
                conversation_blocks[-1]["text"] += "\n</history>"
            else:
                conversation_blocks.append({"text": "</history>"})

        conversation_blocks_text = "\n".join(
            b.get("text", "") for b in conversation_blocks
        )

        content_list: list[dict[str, Any]] = []
        if conversation_blocks_text:
            content_list.append(
                {"type": "text", "text": conversation_blocks_text},
            )
        content_list.extend(media_blocks)

        if content_list:
            formatted_msgs.append({"role": "user", "content": content_list})

        return formatted_msgs

    @staticmethod
    async def _format_system_message(
        msg: Msg,
    ) -> dict[str, Any]:
        """Format system message for DashScope API."""
        return {
            "role": "system",
            "content": msg.get_text_content(),
        }
