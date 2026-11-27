# -*- coding: utf-8 -*-
"""Preserve interleaved Responses API items in assistant history."""
from unittest import IsolatedAsyncioTestCase

from agentscope.formatter import OpenAIResponseFormatter
from agentscope.message import (
    AssistantMsg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
)


class ResponsesOrderTest(IsolatedAsyncioTestCase):
    """Tool calls remain in their original position among other blocks."""

    async def test_text_around_tool_call(self) -> None:
        """Text after a tool call must not move before it."""
        msg = AssistantMsg(
            name="assistant",
            content=[
                TextBlock(text="before"),
                ToolCallBlock(id="call-1", name="lookup", input="{}"),
                TextBlock(text="after"),
            ],
        )
        items = await OpenAIResponseFormatter().format([msg])
        self.assertEqual(
            items,
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "before"},
                    ],
                },
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "lookup",
                    "arguments": "{}",
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "after"},
                    ],
                },
            ],
        )

    async def test_reasoning_between_tool_calls(self) -> None:
        """A reasoning item does not move ahead of a preceding call."""
        reasoning = ThinkingBlock(thinking="thinking")
        reasoning.reasoning_item_id = "rs-1"
        msg = AssistantMsg(
            name="assistant",
            content=[
                ToolCallBlock(id="call-1", name="lookup", input="{}"),
                reasoning,
                ToolCallBlock(id="call-2", name="lookup", input="{}"),
            ],
        )
        items = await OpenAIResponseFormatter().format([msg])
        self.assertEqual(
            [item["type"] for item in items],
            ["function_call", "reasoning", "function_call"],
        )
