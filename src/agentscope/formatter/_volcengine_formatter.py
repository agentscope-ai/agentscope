# -*- coding: utf-8 -*-
"""The Volcengine Ark formatter module."""

from fnmatch import fnmatch
from typing import Any

from pydantic import Field

from ._openai_formatter import _OpenAIFormatterBase
from .._logging import logger
from ..message import (
    Msg,
    TextBlock,
    DataBlock,
    ThinkingBlock,
    HintBlock,
    ToolCallBlock,
    ToolResultBlock,
    URLSource,
    Base64Source,
)


class _VolcengineFormatterBase(_OpenAIFormatterBase):
    """Base formatter with shared Ark multimodal conversion logic."""

    def _format_volcengine_data_block(
        self,
        block: DataBlock,
    ) -> dict[str, Any] | None:
        """Format supported image and video data for the Ark Chat API.

        Args:
            block (`DataBlock`):
                The data block to format.

        Returns:
            `dict[str, Any] | None`:
                The formatted content block, or ``None`` when unsupported.
        """
        if not any(
            fnmatch(block.source.media_type, pattern)
            for pattern in self.supported_input_media_types
        ):
            logger.warning(
                "Unsupported media type %s for Volcengine Ark API. "
                "Supported types: %s. This block will be skipped.",
                block.source.media_type,
                ", ".join(self.supported_input_media_types),
            )
            return None

        main_type = block.source.media_type.split("/")[0]
        if main_type == "image":
            return self._format_image_source(block.source)
        if main_type == "video":
            return self._format_video_source(block.source)

        logger.warning(
            "Unsupported main media type %s for Volcengine Ark API. "
            "This block will be skipped.",
            main_type,
        )
        return None

    def _format_video_source(
        self,
        source: URLSource | Base64Source,
    ) -> dict[str, Any]:
        """Convert a video source to Ark's ``video_url`` format. Ark takes
        the same URL / base64 data URI as images, so reuse that conversion.

        Args:
            source (`URLSource | Base64Source`):
                A remote, local, or base64-encoded video source.

        Returns:
            `dict[str, Any]`:
                A video content block accepted by the Ark Chat API.
        """
        return {
            "type": "video_url",
            "video_url": self._format_image_source(source)["image_url"],
        }


class VolcengineChatFormatter(_VolcengineFormatterBase):
    """The Volcengine Ark formatter for a chatbot conversation.

    The ``role`` field identifies the user and assistant participants.
    """

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain", "image/*", "video/*"],
        description=(
            "The supported input types. Defaults to "
            '``["text/plain", "image/*", "video/*"]``.'
        ),
    )

    # pylint: disable=too-many-branches
    async def format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into Volcengine Ark API format.

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
            reasoning_content_blocks: list = []
            tool_calls = []

            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    content_blocks.append({"type": "text", "text": block.text})

                elif isinstance(block, DataBlock):
                    formatted = self._format_volcengine_data_block(block)
                    if formatted is not None:
                        content_blocks.append(formatted)

                elif isinstance(block, ThinkingBlock):
                    reasoning_content_blocks.append(block.thinking)

                elif isinstance(block, HintBlock):
                    if (
                        content_blocks
                        or tool_calls
                        or reasoning_content_blocks
                    ):
                        msg_flush_hint: dict[str, Any] = {
                            "role": msg.role,
                            "content": content_blocks or None,
                        }
                        if (
                            msg.role == "assistant"
                            and reasoning_content_blocks
                        ):
                            msg_flush_hint["reasoning_content"] = "\n".join(
                                reasoning_content_blocks,
                            )
                        if tool_calls:
                            msg_flush_hint["tool_calls"] = tool_calls
                        messages.append(msg_flush_hint)
                        content_blocks = []
                        reasoning_content_blocks = []
                        tool_calls = []

                    if isinstance(block.hint, str):
                        messages.append(
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": block.hint},
                                ],
                            },
                        )
                    else:
                        hint_parts: list[dict[str, Any]] = []
                        for sub in block.hint:
                            if isinstance(sub, TextBlock):
                                hint_parts.append(
                                    {"type": "text", "text": sub.text},
                                )
                            elif isinstance(sub, DataBlock):
                                formatted_sub = (
                                    self._format_volcengine_data_block(sub)
                                )
                                if formatted_sub is not None:
                                    hint_parts.append(formatted_sub)
                        if hint_parts:
                            messages.append(
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

                elif isinstance(block, ToolResultBlock):
                    if (
                        content_blocks
                        or tool_calls
                        or reasoning_content_blocks
                    ):
                        msg_flush: dict[str, Any] = {
                            "role": msg.role,
                            "content": content_blocks or None,
                        }
                        if (
                            msg.role == "assistant"
                            and reasoning_content_blocks
                        ):
                            msg_flush["reasoning_content"] = "\n".join(
                                reasoning_content_blocks,
                            )
                        if tool_calls:
                            msg_flush["tool_calls"] = tool_calls
                        messages.append(msg_flush)
                        content_blocks = []
                        reasoning_content_blocks = []
                        tool_calls = []

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
                        promoted_content: list[dict[str, Any]] = []
                        for item in multimodal_data:
                            if isinstance(item, TextBlock):
                                promoted_content.append(
                                    {"type": "text", "text": item.text},
                                )
                            elif isinstance(item, DataBlock):
                                formatted_item = (
                                    self._format_volcengine_data_block(item)
                                )
                                if formatted_item is not None:
                                    promoted_content.append(formatted_item)
                        if promoted_content:
                            messages.append(
                                {
                                    "role": "user",
                                    "name": "system-reminder",
                                    "content": promoted_content,
                                },
                            )

                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        type(block),
                    )

            msg_volcengine: dict[str, Any] = {
                "role": msg.role,
                "content": content_blocks or None,
            }

            # Preserve reasoning in multi-turn requests. Ark documents this
            # field as optional, so omit it for non-thinking history.
            if msg.role == "assistant" and reasoning_content_blocks:
                msg_volcengine["reasoning_content"] = "\n".join(
                    reasoning_content_blocks,
                )

            if tool_calls:
                msg_volcengine["tool_calls"] = tool_calls

            if (
                msg_volcengine["content"]
                or msg_volcengine.get("tool_calls")
                or reasoning_content_blocks
            ):
                messages.append(msg_volcengine)

        return messages


class VolcengineMultiAgentFormatter(_VolcengineFormatterBase):
    """
    Volcengine formatter for multi-agent conversations, where more than
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

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain", "image/*", "video/*"],
        description=(
            "The supported input types. Defaults to "
            '``["text/plain", "image/*", "video/*"]``.'
        ),
    )

    async def format(self, msgs: list[Msg]) -> list[dict[str, Any]]:
        """Format input messages into the structure required by the Volcengine
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
        the required format for the Volcengine API."""
        return await VolcengineChatFormatter(
            input_types=self.input_types,
        ).format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Given a sequence of messages without tool calls/results, format
        them into the required format for the Volcengine API."""

        if is_first:
            conversation_history_prompt = self.conversation_history_prompt
        else:
            conversation_history_prompt = ""

        accumulated_text: list[str] = []
        media_blocks: list[dict[str, Any]] = []

        for msg in msgs:
            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    accumulated_text.append(f"{msg.name}: {block.text}")
                elif isinstance(block, DataBlock):
                    formatted = self._format_volcengine_data_block(block)
                    if formatted is not None:
                        media_blocks.append(formatted)

        if not accumulated_text and not media_blocks:
            return []

        content_blocks: list[dict[str, Any]] = []
        if accumulated_text:
            content_blocks.append(
                {
                    "type": "text",
                    "text": (
                        conversation_history_prompt
                        + "<history>\n"
                        + "\n".join(accumulated_text)
                        + "\n</history>"
                    ),
                },
            )
        content_blocks.extend(media_blocks)

        return [{"role": "user", "content": content_blocks}]

    @staticmethod
    async def _format_system_message(
        msg: Msg,
    ) -> dict[str, Any]:
        """Format system message for Volcengine API."""
        return {
            "role": "system",
            "content": msg.get_text_content(),
        }
