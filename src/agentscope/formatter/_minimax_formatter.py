# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches
"""The MiniMax formatter module."""
import json
from typing import Any

from ._truncated_formatter_base import TruncatedFormatterBase
from .._logging import logger
from ..message import Msg, TextBlock, ThinkingBlock, ToolUseBlock, ToolResultBlock
from ..token import TokenCounterBase


class MiniMaxChatFormatter(TruncatedFormatterBase):
    """The MiniMax formatter class for chatbot scenario, where only a user
    and an agent are involved. MiniMax M2.5 uses <think></think> tags for
    reasoning content in the message content field.
    """

    support_tools_api: bool = True

    support_multiagent: bool = False

    support_vision: bool = False

    supported_blocks: list[type] = [
        TextBlock,
        ToolUseBlock,
        ToolResultBlock,
    ]

    async def _format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        self.assert_list_of_msgs(msgs)

        messages: list[dict] = []
        for msg in msgs:
            content_blocks: list = []
            thinking_blocks: list = []
            tool_calls = []

            for block in msg.get_content_blocks():
                typ = block.get("type")
                if typ == "text":
                    content_blocks.append({**block})
                elif typ == "thinking":
                    thinking_blocks.append({**block})

                elif typ == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("id"),
                            "type": "function",
                            "function": {
                                "name": block.get("name"),
                                "arguments": json.dumps(
                                    block.get("input", {}),
                                    ensure_ascii=False,
                                ),
                            },
                        },
                    )

                elif typ == "tool_result":
                    textual_output, _ = self.convert_tool_result_to_string(
                        block.get("output"),
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("id"),
                            "content": textual_output,
                            "name": block.get("name"),
                        },
                    )

                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        typ,
                    )

            text_content = "\n".join(
                content.get("text", "") for content in content_blocks
            )
            thinking_content = "\n".join(
                t.get("thinking", "") for t in thinking_blocks
            )

            full_content = ""
            if thinking_content:
                full_content += f"<think>{thinking_content}</think>"
            if text_content:
                if full_content:
                    full_content += "\n"
                full_content += text_content

            msg_minimax = {
                "role": msg.role,
                "content": full_content or None,
            }

            if tool_calls:
                msg_minimax["tool_calls"] = tool_calls

            if msg_minimax["content"] or msg_minimax.get("tool_calls"):
                messages.append(msg_minimax)

        return messages


class MiniMaxMultiAgentFormatter(TruncatedFormatterBase):
    """MiniMax formatter for multi-agent conversations, where more than
    a user and an agent are involved.
    """

    support_tools_api: bool = True

    support_multiagent: bool = True

    support_vision: bool = False

    supported_blocks: list[type] = [
        TextBlock,
        ToolUseBlock,
        ToolResultBlock,
    ]

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
        super().__init__(token_counter=token_counter, max_tokens=max_tokens)
        self.conversation_history_prompt = conversation_history_prompt

    async def _format_tool_sequence(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        return await MiniMaxChatFormatter().format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        if is_first:
            conversation_history_prompt = self.conversation_history_prompt
        else:
            conversation_history_prompt = ""

        formatted_msgs: list[dict] = []

        conversation_blocks: list = []
        accumulated_text = []
        for msg in msgs:
            for block in msg.get_content_blocks():
                if block["type"] == "text":
                    accumulated_text.append(f"{msg.name}: {block['text']}")

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

        if conversation_blocks:
            formatted_msgs.append(user_message)

        return formatted_msgs
