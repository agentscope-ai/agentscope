# -*- coding: utf-8 -*-
"""The Moonshot AI formatter for agentscope."""
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
    UserMsg,
)


class MoonshotChatFormatter(_OpenAIFormatterBase):
    """The Moonshot AI formatter for chatbot scenario.

    Moonshot's API is OpenAI-compatible, but thinking models (``kimi-k2.6``,
    ``kimi-k2-thinking``) return a ``reasoning_content`` field alongside
    ``content`` in assistant messages.  This formatter preserves that field
    when re-sending assistant messages back to the API so that the
    *Preserved Thinking* feature works correctly in multi-turn conversations.
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
        """Format messages into the Moonshot / OpenAI-compatible API format.

        Behaves identically to :class:`OpenAIChatFormatter` except that
        :class:`ThinkingBlock` content is placed into the ``reasoning_content``
        field of the assistant message dict (required for Preserved Thinking).

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
            content_blocks: list[dict] = []
            reasoning_parts: list[str] = []
            tool_calls: list[dict] = []

            for block in msg.get_content_blocks():
                if isinstance(block, ThinkingBlock):
                    # Preserve reasoning_content for multi-turn
                    # Preserved Thinking (kimi-k2.6 / kimi-k2-thinking)
                    reasoning_parts.append(block.thinking)

                elif isinstance(block, TextBlock):
                    content_blocks.append({"type": "text", "text": block.text})

                elif isinstance(block, DataBlock):
                    formatted = self._format_openai_data_block(
                        block,
                        role=msg.role,
                    )
                    if formatted is not None:
                        content_blocks.append(formatted)

                elif isinstance(block, HintBlock):
                    if content_blocks or tool_calls:
                        msg_moonshot = {
                            "role": msg.role,
                            "name": msg.name,
                            "content": content_blocks or None,
                        }
                        if reasoning_parts:
                            msg_moonshot["reasoning_content"] = "\n".join(
                                reasoning_parts,
                            )
                        if tool_calls:
                            msg_moonshot["tool_calls"] = tool_calls
                        messages.append(msg_moonshot)
                        content_blocks = []
                        reasoning_parts = []
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

                else:
                    logger.warning(
                        "Unsupported block type %s in the message, skipped.",
                        type(block),
                    )

            msg_moonshot = {
                "role": msg.role,
                "name": msg.name,
                "content": content_blocks or None,
            }

            # Moonshot's Preserved Thinking requires `reasoning_content` on ALL
            # assistant messages in multi-turn conversations (None when no
            # thinking took place), so that the model can continue its chain
            # of thought correctly.
            if msg.role == "assistant":
                msg_moonshot["reasoning_content"] = (
                    "\n".join(reasoning_parts) if reasoning_parts else ""
                )

            if tool_calls:
                msg_moonshot["tool_calls"] = tool_calls

            if msg_moonshot["content"] or msg_moonshot.get("tool_calls"):
                messages.append(msg_moonshot)

            i += 1

        return messages


class MoonshotMultiAgentFormatter(_OpenAIFormatterBase):
    """Formatter for the Moonshot AI API in multi-agent conversations.

    Moonshot's API is OpenAI-compatible, so the multi-agent history collapsing
    strategy is the same as :class:`OpenAIMultiAgentFormatter`.  Tool
    sequences are delegated to :class:`MoonshotChatFormatter` so that
    ``reasoning_content`` is preserved correctly for multi-turn
    *Preserved Thinking* conversations.

    .. note:: Telling the assistant's name in the system prompt is important
        in multi-agent conversations so that the model knows which role it
        is playing.
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
        """Format input messages into the Moonshot AI API format for
        multi-agent conversations.

        Non-tool messages from all agents are collapsed into a single user
        message with ``<history></history>`` tags.  Tool call / result
        sequences are delegated to :class:`MoonshotChatFormatter`.

        Args:
            msgs (`list[Msg]`):
                The list of message objects to format.

        Returns:
            `list[dict[str, Any]]`:
                The formatted messages as a list of dictionaries.
        """
        self.assert_list_of_msgs(msgs)

        formatted_msgs: list[dict] = []
        start_index = 0
        if msgs and msgs[0].role == "system":
            formatted_msgs.append(
                await self._format_system_message(msgs[0]),
            )
            start_index = 1

        is_first_agent_message = True
        async for typ, group in self._group_messages(msgs[start_index:]):
            if typ == "tool_sequence":
                formatted_msgs.extend(
                    await MoonshotChatFormatter(
                        input_types=self.input_types,
                    ).format(group),
                )
            elif typ == "agent_message":
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
        """Format a sequence of tool-related messages using
        MoonshotChatFormatter."""
        return await MoonshotChatFormatter(
            input_types=self.input_types,
        ).format(msgs)

    async def _format_agent_message(
        self,
        msgs: list[Msg],
        is_first: bool = True,
    ) -> list[dict[str, Any]]:
        """Collapse agent messages into a ``<history>`` user message."""
        if is_first:
            conversation_history_prompt = self.conversation_history_prompt
        else:
            conversation_history_prompt = ""

        accumulated_text: list[str] = []
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

        if not accumulated_text and not media_blocks:
            return []

        history_text = "\n".join(accumulated_text)
        if history_text:
            history_text = (
                conversation_history_prompt
                + "<history>\n"
                + history_text
                + "\n</history>"
            )

        content_list: list[dict[str, Any]] = []
        if history_text:
            content_list.append({"type": "text", "text": history_text})
        content_list.extend(media_blocks)

        return [{"role": "user", "content": content_list}]

    @staticmethod
    async def _format_system_message(msg: Msg) -> dict[str, Any]:
        """Format a system message for the Moonshot AI API."""
        return {
            "role": "system",
            "content": msg.get_text_content(),
        }
