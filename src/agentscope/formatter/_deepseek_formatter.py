# -*- coding: utf-8 -*-
"""The DeepSeek formatter module."""
from typing import Any

from ._formatter_base import FormatterBase
from .._logging import logger
from ..message import (
    Msg,
    TextBlock,
    ThinkingBlock,
    HintBlock,
    ToolCallBlock,
    ToolResultBlock,
)


class DeepSeekChatFormatter(FormatterBase):
    """The DeepSeek formatter class for chatbot scenario, where only a user
    and an agent are involved. We use the `role` field to identify different
    entities in the conversation.
    """

    def __init__(
        self,
        supported_input_media_types: list[str] | None = None,
    ) -> None:
        super().__init__(
            supported_input_media_types=supported_input_media_types or [],
        )

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

            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    content_blocks.append({"type": "text", "text": block.text})

                elif isinstance(block, ThinkingBlock):
                    reasoning_content_blocks.append(block.thinking)

                elif isinstance(block, HintBlock):
                    pass  # DeepSeek does not support hint blocks

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
                    textual_output, _ = self.convert_tool_result_to_string(
                        block.output,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.id,
                            "content": textual_output,
                            "name": block.name,
                        },
                    )

                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        type(block),
                    )

            content_msg = "\n".join(b.get("text", "") for b in content_blocks)

            msg_deepseek: dict[str, Any] = {
                "role": msg.role,
                "content": content_msg or None,
            }

            if reasoning_content_blocks:
                msg_deepseek["reasoning_content"] = "\n".join(
                    reasoning_content_blocks,
                )

            if tool_calls:
                msg_deepseek["tool_calls"] = tool_calls

            if msg_deepseek["content"] or msg_deepseek.get("tool_calls"):
                messages.append(msg_deepseek)

        return messages


class DeepSeekMultiAgentFormatter(FormatterBase):
    """
    DeepSeek formatter for multi-agent conversations, where more than
    a user and an agent are involved.
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
        """Initialize the DeepSeek multi-agent formatter.

        Args:
            conversation_history_prompt (`str`):
                The prompt to use for the conversation history section.
            supported_input_media_types (`list[str] | None`, optional):
                The list of supported input media types. Defaults to ``[]``
                (DeepSeek does not support multimodal input).
        """
        super().__init__(
            supported_input_media_types=supported_input_media_types or [],
        )
        self.conversation_history_prompt = conversation_history_prompt

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
        the required format for the DeepSeek API."""
        return await DeepSeekChatFormatter(
            supported_input_media_types=self.supported_input_media_types,
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
        accumulated_text = []

        for msg in msgs:
            for block in msg.get_content_blocks():
                if isinstance(block, TextBlock):
                    accumulated_text.append(f"{msg.name}: {block.text}")

        conversation_blocks_text = ""
        if accumulated_text:
            conversation_blocks_text = (
                conversation_history_prompt
                + "<history>\n"
                + "\n".join(accumulated_text)
                + "\n</history>"
            )

        if conversation_blocks_text:
            formatted_msgs.append(
                {
                    "role": "user",
                    "content": conversation_blocks_text,
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
