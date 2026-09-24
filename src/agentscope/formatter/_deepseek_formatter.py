# -*- coding: utf-8 -*-
"""The DeepSeek formatter module."""
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
)


_DEEPSEEK_IMAGE_TYPES = (
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
)


class DeepSeekChatFormatter(_OpenAIFormatterBase):
    """The DeepSeek formatter class for chatbot scenario, where only a user
    and an agent are involved. We use the `role` field to identify different
    entities in the conversation.
    """

    input_types: list[str] = Field(
        default_factory=lambda: ["text/plain", "image/*"],
        description=(
            "The supported input types. Defaults to "
            '``["text/plain", "image/*"]``.'
        ),
    )

    @property
    def supported_input_media_types(self) -> list[str]:
        """Return configured media types that DeepSeek can encode."""
        configured = super().supported_input_media_types
        return [
            media_type
            for media_type in _DEEPSEEK_IMAGE_TYPES
            if any(fnmatch(media_type, pattern) for pattern in configured)
        ]

    @staticmethod
    def _content_from_blocks(
        blocks: list[dict[str, Any]],
    ) -> str | list[dict[str, Any]]:
        """Keep text-only content as a string and multimodal content as a
        list of content blocks."""
        if any(block["type"] == "image_url" for block in blocks):
            return blocks
        return "\n".join(block.get("text", "") for block in blocks)

    def _format_deepseek_data_block(
        self,
        block: DataBlock,
    ) -> dict[str, Any] | None:
        """Format a supported DeepSeek image data block."""
        if block.source.media_type not in self.supported_input_media_types:
            logger.warning(
                "Unsupported media type %s for DeepSeek API. "
                "Supported image types: %s. This block will be skipped.",
                block.source.media_type,
                ", ".join(self.supported_input_media_types) or "none",
            )
            return None
        return self._format_openai_data_block(block)

    # pylint: disable=too-many-branches
    async def format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format message objects into DeepSeek API format.

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
            pending_media: list[dict[str, Any]] = []

            for block in msg.get_content_blocks():
                if pending_media and not isinstance(block, ToolResultBlock):
                    messages.extend(pending_media)
                    pending_media = []

                if isinstance(block, TextBlock):
                    content_blocks.append({"type": "text", "text": block.text})

                elif isinstance(block, DataBlock) and msg.role == "user":
                    formatted = self._format_deepseek_data_block(block)
                    if formatted is not None:
                        content_blocks.append(formatted)

                elif isinstance(block, DataBlock):
                    # pylint: disable-next=logging-fstring-interpolation
                    logger.warning(
                        f"DataBlock in {msg.role} role is not supported by "
                        "DeepSeek API, skipped.",
                    )

                elif isinstance(block, ThinkingBlock):
                    reasoning_content_blocks.append(block.thinking)

                elif isinstance(block, HintBlock):
                    if (
                        content_blocks
                        or tool_calls
                        or reasoning_content_blocks
                    ):
                        content = self._content_from_blocks(content_blocks)
                        msg_flush_hint: dict[str, Any] = {
                            "role": msg.role,
                            "content": content or (None if tool_calls else ""),
                        }
                        if msg.role == "assistant":
                            msg_flush_hint["reasoning_content"] = (
                                "\n".join(reasoning_content_blocks)
                                if reasoning_content_blocks
                                else ""
                            )
                        if tool_calls:
                            msg_flush_hint["tool_calls"] = tool_calls
                        messages.append(msg_flush_hint)
                        content_blocks = []
                        reasoning_content_blocks = []
                        tool_calls = []

                    if isinstance(block.hint, str):
                        messages.append(
                            {"role": "user", "content": block.hint},
                        )
                    else:
                        hint_parts: list[dict[str, Any]] = []
                        for sub in block.hint:
                            if isinstance(sub, TextBlock):
                                hint_parts.append(
                                    {"type": "text", "text": sub.text},
                                )
                            elif isinstance(sub, DataBlock):
                                formatted = self._format_deepseek_data_block(
                                    sub,
                                )
                                if formatted is not None:
                                    hint_parts.append(formatted)
                                else:
                                    hint_parts.append(
                                        {
                                            "type": "text",
                                            "text": (
                                                f"[{sub.source.media_type} "
                                                "attached, not supported by "
                                                "this provider]"
                                            ),
                                        },
                                    )
                        if hint_parts:
                            messages.append(
                                {
                                    "role": "user",
                                    "content": self._content_from_blocks(
                                        hint_parts,
                                    ),
                                },
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
                        content = self._content_from_blocks(content_blocks)
                        msg_flush: dict[str, Any] = {
                            "role": msg.role,
                            "content": content or (None if tool_calls else ""),
                        }
                        if msg.role == "assistant":
                            msg_flush["reasoning_content"] = (
                                "\n".join(reasoning_content_blocks)
                                if reasoning_content_blocks
                                else ""
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
                        promo_content: list[dict[str, Any]] = []
                        for item in multimodal_data:
                            if isinstance(item, TextBlock):
                                promo_content.append(
                                    {"type": "text", "text": item.text},
                                )
                            elif isinstance(item, DataBlock):
                                formatted = self._format_deepseek_data_block(
                                    item,
                                )
                                if formatted is not None:
                                    promo_content.append(formatted)
                        if promo_content:
                            pending_media.append(
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

            messages.extend(pending_media)

            content_msg = self._content_from_blocks(content_blocks)

            msg_deepseek: dict[str, Any] = {
                "role": msg.role,
                "content": content_msg or (None if tool_calls else ""),
            }

            # DeepSeek requires `reasoning_content` to be present on ALL
            # assistant messages in multi-turn conversations that use thinking
            # mode.  When switching from a non-thinking model, historical
            # messages will have no ThinkingBlock, so we always include the
            # field (as None) for assistant messages to keep the context valid.
            if msg.role == "assistant":
                msg_deepseek["reasoning_content"] = (
                    "\n".join(reasoning_content_blocks)
                    if reasoning_content_blocks
                    else ""
                )

            if tool_calls:
                msg_deepseek["tool_calls"] = tool_calls

            if (
                msg_deepseek["content"]
                or msg_deepseek.get("tool_calls")
                or reasoning_content_blocks
            ):
                messages.append(msg_deepseek)

        return messages


class DeepSeekMultiAgentFormatter(DeepSeekChatFormatter):
    """
    DeepSeek formatter for multi-agent conversations, where more than
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
        default_factory=lambda: ["text/plain", "image/*"],
        description=(
            "The supported input types. Defaults to "
            '``["text/plain", "image/*"]``.'
        ),
    )

    async def format(self, msgs: list[Msg]) -> list[dict[str, Any]]:
        """Format input messages into the structure required by the DeepSeek
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
                    formatted_group = await self._format_agent_message(
                        group,
                        is_first_agent_message,
                    )
                    formatted_msgs.extend(formatted_group)
                    if formatted_group:
                        is_first_agent_message = False

        return formatted_msgs

    async def _format_tool_sequence(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Given a sequence of tool call/result messages, format them into
        the required format for the DeepSeek API."""
        return await DeepSeekChatFormatter(
            input_types=self.input_types,
        ).format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Given a sequence of messages without tool calls/results, format
        them into the required format for the DeepSeek API."""

        if is_first:
            conversation_history_prompt = self.conversation_history_prompt
        else:
            conversation_history_prompt = ""

        formatted_msgs: list[dict] = []
        content_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": f"{conversation_history_prompt}<history>\n",
            },
        ]
        has_history = False
        has_image = False

        for msg in msgs:
            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    content_blocks[-1]["text"] += f"{msg.name}: {block.text}\n"
                    has_history = True
                elif isinstance(block, DataBlock):
                    formatted = self._format_deepseek_data_block(block)
                    if formatted is not None:
                        content_blocks[-1]["text"] += f"{msg.name}: "
                        content_blocks.extend(
                            [
                                formatted,
                                {"type": "text", "text": "\n"},
                            ],
                        )
                        has_history = True
                        has_image = True

        if has_history:
            content_blocks[-1]["text"] += "</history>"
            formatted_msgs.append(
                {
                    "role": "user",
                    "content": (
                        content_blocks
                        if has_image
                        else content_blocks[0]["text"]
                    ),
                },
            )

        return formatted_msgs

    @staticmethod
    async def _format_system_message(
        msg: Msg,
    ) -> dict[str, Any]:
        """Format system message for DeepSeek API."""
        return {
            "role": "system",
            "content": msg.get_text_content(),
        }
