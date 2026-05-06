# -*- coding: utf-8 -*-
"""The Kimi (Moonshot AI) formatter for agentscope."""
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


class KimiChatFormatter(_OpenAIFormatterBase):
    """The Kimi formatter for chatbot scenario.

    Kimi's API is OpenAI-compatible, but thinking models (``kimi-k2.6``,
    ``kimi-k2-thinking``) return a ``reasoning_content`` field alongside
    ``content`` in assistant messages.  This formatter preserves that field
    when re-sending assistant messages back to the API so that Kimi's
    *Preserved Thinking* feature works correctly in multi-turn conversations.
    """

    supported_input_media_types: list[str] = Field(
        default_factory=lambda: ["image/*", "audio/*"],
        description=(
            "The supported input media types. "
            'Defaults to ``["image/*", "audio/*"]``.'
        ),
    )

    async def format(
        self,
        msgs: list[Msg],
    ) -> list[dict[str, Any]]:
        """Format messages into the Kimi / OpenAI-compatible API format.

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
                    # Preserve reasoning_content for Kimi's multi-turn
                    # Preserved Thinking feature (kimi-k2.6 / kimi-k2-thinking)
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
                        msg_kimi = {
                            "role": msg.role,
                            "name": msg.name,
                            "content": content_blocks or None,
                        }
                        if reasoning_parts:
                            msg_kimi["reasoning_content"] = "\n".join(
                                reasoning_parts,
                            )
                        if tool_calls:
                            msg_kimi["tool_calls"] = tool_calls
                        messages.append(msg_kimi)
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

            msg_kimi = {
                "role": msg.role,
                "name": msg.name,
                "content": content_blocks or None,
            }

            # Kimi's Preserved Thinking requires `reasoning_content` on ALL
            # assistant messages in multi-turn conversations (None when no
            # thinking took place), so that the model can continue its chain
            # of thought correctly.
            if msg.role == "assistant":
                msg_kimi["reasoning_content"] = (
                    "\n".join(reasoning_parts) if reasoning_parts else ""
                )

            if tool_calls:
                msg_kimi["tool_calls"] = tool_calls

            if msg_kimi["content"] or msg_kimi.get("tool_calls"):
                messages.append(msg_kimi)

            i += 1

        return messages
