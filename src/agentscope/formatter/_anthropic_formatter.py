# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches
"""The Anthropic formatter module."""

from typing import Any

from ._truncated_formatter_base import TruncatedFormatterBase
from .._logging import logger
from ..message import Msg, TextBlock, ImageBlock, ToolUseBlock, ToolResultBlock
from ..token import TokenCounterBase


class AnthropicChatFormatter(TruncatedFormatterBase):
    """The Anthropic formatter class for chatbot scenario, where only a user
    and an agent are involved. We use the `role` field to identify different
    entities in the conversation.
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

    async def _format(
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
        for index, msg in enumerate(msgs):
            content_blocks = []

            for block in msg.get_content_blocks():
                typ = block.get("type")
                if typ in ["thinking", "text", "image"]:
                    content_blocks.append({**block})

                elif typ == "tool_use":
                    content_blocks.append(
                        {
                            "id": block.get("id"),
                            "type": "tool_use",
                            "name": block.get("name"),
                            "input": block.get("input", {}),
                        },
                    )

                elif typ == "tool_result":
                    output = block.get("output")
                    if output is None:
                        content_value = [{"type": "text", "text": None}]
                    elif isinstance(output, list):
                        content_value = output
                    else:
                        content_value = [{"type": "text", "text": str(output)}]
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.get("id"),
                                    "content": content_value,
                                },
                            ],
                        },
                    )
                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        typ,
                    )

            # Claude only allow the first message to be system message
            if msg.role == "system" and index != 0:
                role = "user"
            else:
                role = msg.role

            msg_anthropic = {
                "role": role,
                "content": content_blocks or None,
            }

            # When both content and tool_calls are None, skipped
            if msg_anthropic["content"] or msg_anthropic.get("tool_calls"):
                messages.append(msg_anthropic)

        return messages


class AnthropicMultiAgentFormatter(TruncatedFormatterBase):
    """
    Anthropic formatter for multi-agent conversations, where more than
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
        """Initialize the DashScope multi-agent formatter.

        Args:
            conversation_history_prompt (`str`):
                The prompt to use for the conversation history section.
        """
        super().__init__(token_counter=token_counter, max_tokens=max_tokens)
        self.conversation_history_prompt = conversation_history_prompt

    async def _format_tool_sequence(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Given a sequence of tool call/result messages, format them into
        the required format for the Anthropic API."""
        return await AnthropicChatFormatter().format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Given a sequence of messages without tool calls/results, format
        them into the required format for the Anthropic API."""

        if is_first:
            conversation_history_prompt = self.conversation_history_prompt
        else:
            conversation_history_prompt = ""

        # Format into required Anthropic format
        formatted_msgs: list[dict] = []

        # Collect the multimodal files
        conversation_blocks: list = []
        accumulated_text = []
        for msg in msgs:
            for block in msg.get_content_blocks():
                if block["type"] == "text":
                    accumulated_text.append(f"{msg.name}: {block['text']}")

                elif block["type"] == "image":
                    # Handle the accumulated text as a single block
                    if accumulated_text:
                        conversation_blocks.append(
                            {
                                "text": "\n".join(accumulated_text),
                                "type": "text",
                            },
                        )
                        accumulated_text.clear()

                    conversation_blocks.append({**block})

        if accumulated_text:
            conversation_blocks.append(
                {
                    "text": "\n".join(accumulated_text),
                    "type": "text",
                },
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
                        "type": "text",
                        "text": conversation_history_prompt + "<history>\n",
                    },
                )

            if conversation_blocks[-1].get("text"):
                conversation_blocks[-1]["text"] += "\n</history>"

            else:
                conversation_blocks.append(
                    {"type": "text", "text": "</history>"},
                )

        if conversation_blocks:
            formatted_msgs.append(
                {
                    "role": "user",
                    "content": conversation_blocks,
                },
            )

        return formatted_msgs
