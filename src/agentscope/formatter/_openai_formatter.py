# -*- coding: utf-8 -*-
"""The OpenAI formatter for agentscope."""
import base64
from abc import ABC
from fnmatch import fnmatch
from typing import Any
from urllib.parse import urlparse

import requests
from pydantic import Field

from ._formatter_base import FormatterBase
from .._logging import logger
from ..message import (
    Msg,
    URLSource,
    TextBlock,
    DataBlock,
    Base64Source,
    ToolCallBlock,
    ToolResultBlock,
    HintBlock,
    ThinkingBlock,
    UserMsg,
)


class _OpenAIFormatterBase(FormatterBase, ABC):
    """Base class for OpenAI formatters, providing shared data block
    formatting logic."""

    def _format_openai_data_block(
        self,
        block: DataBlock,
        role: str = "user",
    ) -> dict[str, Any] | None:
        """Format a DataBlock into the required format for OpenAI API.

        For image blocks, URLs are returned as-is (or converted to base64 for
        local ``file://`` paths). For audio blocks, data is always converted
        to base64 as required by the OpenAI input_audio format.

        Args:
            block (`DataBlock`):
                The DataBlock to format.
            role (`str`, defaults to ``"user"``):
                The role of the message that contains this block. Audio blocks
                in assistant messages are skipped to avoid errors in subsequent
                model calls.

        Returns:
            `dict[str, Any] | None`:
                A dictionary in OpenAI API format, or ``None`` if the block
                should be skipped.
        """
        if not any(
            fnmatch(block.source.media_type, pattern)
            for pattern in self.supported_input_media_types
        ):
            logger.warning(
                "Unsupported media type %s for OpenAI API. "
                "Supported types: %s. This block will be skipped.",
                block.source.media_type,
                ", ".join(self.supported_input_media_types),
            )
            return None

        main_type = block.source.media_type.split("/")[0]

        if main_type == "image":
            return self._format_image_source(block.source)

        if main_type == "audio":
            # Filter out audio content when the multimodal model outputs both
            # text and audio, to prevent errors in subsequent model calls
            if role == "assistant":
                return None
            return self._format_audio_source(block.source)

        logger.warning(
            "Unsupported main media type %s for OpenAI API. "
            "This block will be skipped.",
            main_type,
        )
        return None

    @staticmethod
    def _format_image_source(
        source: URLSource | Base64Source,
    ) -> dict[str, Any]:
        """Convert an image source to OpenAI image_url format.

        Local ``file://`` URLs are read from disk and converted to base64
        data URIs. Remote URLs are passed through unchanged.

        Args:
            source (`URLSource | Base64Source`):
                The image source to convert.

        Returns:
            `dict[str, Any]`:
                A dictionary with ``"type": "image_url"`` in OpenAI format.
        """
        if isinstance(source, Base64Source):
            url = f"data:{source.media_type};base64,{source.data}"

        elif isinstance(source, URLSource):
            url_str = str(source.url)
            if url_str.startswith("file://"):
                # Local file — read and encode as base64 data URI
                local_path = url_str.removeprefix("file://")
                with open(local_path, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode("utf-8")
                url = f"data:{source.media_type};base64,{encoded}"
            else:
                # Remote URL — pass through as-is
                url = url_str

        else:
            raise ValueError(f"Unsupported image source type: {type(source)}")

        return {
            "type": "image_url",
            "image_url": {"url": url},
        }

    @staticmethod
    def _format_audio_source(
        source: URLSource | Base64Source,
    ) -> dict[str, Any]:
        """Convert an audio source to OpenAI input_audio format.

        Local ``file://`` URLs are read from disk. Remote URLs are downloaded.
        Only ``wav`` and ``mp3`` formats are supported by the OpenAI API.

        Args:
            source (`URLSource | Base64Source`):
                The audio source to convert.

        Returns:
            `dict[str, Any]`:
                A dictionary with ``"type": "input_audio"`` in OpenAI format.
        """
        if isinstance(source, Base64Source):
            media_type = source.media_type
            if media_type not in ["audio/wav", "audio/mp3"]:
                raise TypeError(
                    f"Unsupported audio media type: {media_type}, "
                    "only audio/wav and audio/mp3 are supported.",
                )
            return {
                "type": "input_audio",
                "input_audio": {
                    "data": source.data,
                    "format": media_type.split("/")[-1],
                },
            }

        if isinstance(source, URLSource):
            url_str = str(source.url)
            if url_str.startswith("file://"):
                # Local file
                local_path = url_str.removeprefix("file://")
                extension = local_path.rsplit(".", 1)[-1].lower()
                if extension not in ["wav", "mp3"]:
                    raise TypeError(
                        f"Unsupported audio file extension: {extension}, "
                        "wav and mp3 are supported.",
                    )
                with open(local_path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("utf-8")
            else:
                # Remote URL — download and encode
                parsed = urlparse(url_str)
                extension = parsed.path.rsplit(".", 1)[-1].lower()
                if extension not in ["wav", "mp3"]:
                    raise TypeError(
                        f"Unsupported audio file extension: {extension}, "
                        "wav and mp3 are supported.",
                    )
                response = requests.get(url_str)
                response.raise_for_status()
                data = base64.b64encode(response.content).decode("utf-8")

            return {
                "type": "input_audio",
                "input_audio": {
                    "data": data,
                    "format": extension,
                },
            }

        raise TypeError(f"Unsupported audio source type: {type(source)}.")


class OpenAIChatFormatter(_OpenAIFormatterBase):
    """The OpenAI formatter class for chatbot scenario, where only a user
    and an agent are involved. We use the `name` field in OpenAI API to
    identify different entities in the conversation.
    """

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain", "image/*", "audio/*"],
        description=(
            "The supported input types. "
            'Defaults to ``["text/plain", "image/*", "audio/*"]``.'
        ),
    )

    async def format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into OpenAI API required format.

        Args:
            msgs (`list[Msg]`):
                The list of Msg objects to format.

        Returns:
            `list[dict[str, Any]]`:
                A list of dictionaries, where each dictionary has "name",
                "role", and "content" keys.
        """
        self.assert_list_of_msgs(msgs)

        messages: list[dict] = []
        i = 0
        while i < len(msgs):
            msg = msgs[i]
            content_blocks = []
            tool_calls = []

            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    content_blocks.append({"type": "text", "text": block.text})

                elif isinstance(block, DataBlock):
                    formatted = self._format_openai_data_block(
                        block,
                        role=msg.role,
                    )
                    if formatted is not None:
                        content_blocks.append(formatted)

                elif isinstance(block, HintBlock):
                    # Insert a new user message with the hint content right
                    # after the current message, and go on processing the
                    # rest of the blocks in the current message
                    if content_blocks or tool_calls:
                        msg_openai = {
                            "role": msg.role,
                            "name": msg.name,
                            "content": content_blocks or None,
                        }
                        if tool_calls:
                            msg_openai["tool_calls"] = tool_calls
                        messages.append(msg_openai)
                        content_blocks = []
                        tool_calls = []

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

                elif isinstance(block, ToolResultBlock):
                    (
                        textual_output,
                        multimodal_data,
                    ) = self.convert_tool_result_to_string(block.output)

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.id,
                            "content": textual_output,
                            "name": block.name,
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

                elif isinstance(block, ThinkingBlock):
                    # OpenAI API does not accept reasoning/thinking content
                    # in conversation history — skip thinking blocks silently.
                    pass

                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        type(block),
                    )

            msg_openai = {
                "role": msg.role,
                "name": msg.name,
                "content": content_blocks or None,
            }

            if tool_calls:
                msg_openai["tool_calls"] = tool_calls

            # When both content and tool_calls are None, skipped
            if msg_openai["content"] or msg_openai.get("tool_calls"):
                messages.append(msg_openai)

            # Move to next message
            i += 1

        return messages


class OpenAIMultiAgentFormatter(_OpenAIFormatterBase):
    """
    OpenAI formatter for multi-agent conversations, where more than
    a user and an agent are involved.

    .. tip:: This formatter is compatible with OpenAI API and
        OpenAI-compatible services like vLLM, Azure OpenAI, and others.
    """

    conversation_history_prompt: str = Field(
        default=(
            "# Conversation History\n"
            "The content between <history></history> tags contains "
            "your conversation history\n"
        ),
        description="The prompt to use for the conversation history section.",
    )

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain", "image/*", "audio/*"],
        description=(
            "The supported input types. "
            'Defaults to ``["text/plain", "image/*", "audio/*"]``.'
        ),
    )

    async def format(self, msgs: list[Msg]) -> list[dict[str, Any]]:
        """Format input messages into the structure required by the OpenAI API
        for multi-agent conversations."""
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
        the required format for the OpenAI API."""
        return await OpenAIChatFormatter(
            input_types=self.input_types,
        ).format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Given a sequence of messages without tool calls/results, format
        them into the required format for the OpenAI API."""

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
                    formatted = self._format_openai_data_block(
                        block,
                        role=msg.role,
                    )
                    if formatted is not None:
                        media_blocks.append(formatted)

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
        """Format system message for OpenAI API."""
        return {
            "role": "system",
            "content": msg.get_text_content(),
        }
