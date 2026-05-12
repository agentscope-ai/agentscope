# -*- coding: utf-8 -*-
"""Comprehensive formatter unit tests for OpenAIResponseFormatter and
OpenAIResponseMultiAgentFormatter, following the reference test style with
exact ground-truth comparisons.

Key differences from OpenAI Chat formatter:
  - Text content type is "input_text" (not "text").
  - Image content type is "input_image" with flat "image_url" string.
  - Tool calls become top-level "function_call" items (not nested in a msg).
  - Tool results become top-level "function_call_output" items.
  - ThinkingBlock: only echoed when it has a "reasoning_item_id" attribute.
"""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import shortuuid

from agentscope.formatter import (
    OpenAIResponseFormatter,
    OpenAIResponseMultiAgentFormatter,
)
from agentscope.message import (
    Msg,
    TextBlock,
    DataBlock,
    ToolCallBlock,
    ToolResultBlock,
    Base64Source,
    URLSource,
    ThinkingBlock,
)
from agentscope.message._block import ToolResultState


_FIXED_ID = "TESTID1234567"


class TestOpenAIResponseFormatter(IsolatedAsyncioTestCase):
    """Comprehensive tests for OpenAI Responses API formatters."""

    async def asyncSetUp(self) -> None:
        """Set up shared message fixtures and expected ground-truth dicts."""
        _img_src = URLSource(
            url="https://example.com/image.png",
            media_type="image/png",
        )
        self.image_url = str(_img_src.url)

        self.image_b64 = "ZmFrZSBpbWFnZSBkYXRh"
        self.image_data_uri = f"data:image/png;base64,{self.image_b64}"

        # ---------------------------------------------------------------
        # Message fixtures (no audio to avoid downloads)
        # ---------------------------------------------------------------
        self.msgs_system = [
            Msg(
                name="system",
                content="You're a helpful assistant.",
                role="system",
            ),
        ]

        self.msgs_conversation = [
            Msg(
                name="user",
                content=[
                    TextBlock(
                        type="text",
                        text="What is the capital of France?",
                    ),
                    DataBlock(
                        source=URLSource(
                            url=self.image_url,
                            media_type="image/png",
                        ),
                    ),
                ],
                role="user",
            ),
            Msg(
                name="assistant",
                content="The capital of France is Paris.",
                role="assistant",
            ),
            Msg(
                name="user",
                content="What is the capital of Germany?",
                role="user",
            ),
            Msg(
                name="assistant",
                content="The capital of Germany is Berlin.",
                role="assistant",
            ),
            Msg(
                name="user",
                content="What is the capital of Japan?",
                role="user",
            ),
        ]

        self.msgs_tools = [
            Msg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call_1",
                        name="get_capital",
                        input='{"country": "Japan"}',
                    ),
                ],
                role="assistant",
            ),
            Msg(
                name="tool",
                content=[
                    ToolResultBlock(
                        id="call_1",
                        name="get_capital",
                        output=[
                            TextBlock(
                                type="text",
                                text="The capital of Japan is Tokyo.",
                            ),
                        ],
                        state=ToolResultState.SUCCESS,
                    ),
                ],
                role="assistant",
            ),
            Msg(
                name="assistant",
                content="The capital of Japan is Tokyo.",
                role="assistant",
            ),
        ]

        # ---------------------------------------------------------------
        # Ground truth: OpenAIResponseFormatter
        #   - Text: {"type": "input_text", "text": ...}
        #   - Image: {"type": "input_image", "image_url": url_string}
        #   - ToolCallBlock → top-level {"type": "function_call", ...} item
        #   - ToolResultBlock → top-level {"type": "function_call_output", ...}
        #   - No "name" field on messages.
        # ---------------------------------------------------------------
        self.gt_chat = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": "You're a helpful assistant.",
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "What is the capital of France?",
                    },
                    {"type": "input_image", "image_url": self.image_url},
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "input_text",
                        "text": "The capital of France is Paris.",
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "What is the capital of Germany?",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "input_text",
                        "text": "The capital of Germany is Berlin.",
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "What is the capital of Japan?",
                    },
                ],
            },
            {
                "type": "function_call",
                "id": "call_1",
                "call_id": "call_1",
                "name": "get_capital",
                "arguments": '{"country": "Japan"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "The capital of Japan is Tokyo.",
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "input_text",
                        "text": "The capital of Japan is Tokyo.",
                    },
                ],
            },
        ]

        # ---------------------------------------------------------------
        # Ground truth: OpenAIResponseMultiAgentFormatter
        #   - System: {"role": "system", "content": plain_string}
        #   - Conversation history: input_text with history wrapping.
        #   - Tool sequences use OpenAIResponseFormatter (function_call /
        #     function_call_output top-level items).
        # ---------------------------------------------------------------
        _hist_prompt = (
            OpenAIResponseMultiAgentFormatter().conversation_history_prompt
        )

        _conv_text = (
            "user: What is the capital of France?\n"
            "assistant: The capital of France is Paris.\n"
            "user: What is the capital of Germany?\n"
            "assistant: The capital of Germany is Berlin.\n"
            "user: What is the capital of Japan?"
        )

        _gt_trailing_asst_nonfirst = {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "<history>\n"
                        "assistant: The capital of Japan is Tokyo.\n"
                        "</history>"
                    ),
                },
            ],
        }
        self._gt_trailing_asst_first = {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        _hist_prompt + "<history>\n"
                        "assistant: The capital of Japan is Tokyo.\n"
                        "</history>"
                    ),
                },
            ],
        }

        self._gt_tool_call = {
            "type": "function_call",
            "id": "call_1",
            "call_id": "call_1",
            "name": "get_capital",
            "arguments": '{"country": "Japan"}',
        }
        self._gt_tool_result = {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "The capital of Japan is Tokyo.",
        }

        self.gt_multiagent = [
            {
                "role": "system",
                "content": "You're a helpful assistant.",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            _hist_prompt
                            + "<history>\n"
                            + _conv_text
                            + "\n</history>"
                        ),
                    },
                    {"type": "input_image", "image_url": self.image_url},
                ],
            },
            self._gt_tool_call,
            self._gt_tool_result,
            _gt_trailing_asst_nonfirst,
        ]

    # -------------------------------------------------------------------
    # OpenAIResponseFormatter tests
    # -------------------------------------------------------------------

    async def test_chat_formatter(self) -> None:
        """Chat formatter produces exact output for various subsets."""
        fmt = OpenAIResponseFormatter()
        self.maxDiff = None

        # Full history
        res = await fmt.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )
        self.assertListEqual(self.gt_chat, res)

        # Without system
        res = await fmt.format([*self.msgs_conversation, *self.msgs_tools])
        self.assertListEqual(self.gt_chat[1:], res)

        # Without conversation
        res = await fmt.format([*self.msgs_system, *self.msgs_tools])
        self.assertListEqual(
            [self.gt_chat[0]] + self.gt_chat[-len(self.msgs_tools) :],
            res,
        )

        # Without tools
        res = await fmt.format([*self.msgs_system, *self.msgs_conversation])
        self.assertListEqual(self.gt_chat[: -len(self.msgs_tools)], res)

        # Empty
        res = await fmt.format([])
        self.assertListEqual([], res)

    async def test_chat_formatter_base64_image(self) -> None:
        """Base64-encoded image becomes an input_image item with data URI."""
        fmt = OpenAIResponseFormatter()
        msgs = [
            Msg(
                name="user",
                content=[
                    TextBlock(type="text", text="What's in this image?"),
                    DataBlock(
                        source=Base64Source(
                            type="base64",
                            data=self.image_b64,
                            media_type="image/png",
                        ),
                    ),
                ],
                role="user",
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "What's in this image?",
                        },
                        {
                            "type": "input_image",
                            "image_url": self.image_data_uri,
                        },
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_thinking_dropped_without_reasoning_item_id(
        self,
    ) -> None:
        """ThinkingBlock without reasoning_item_id is silently skipped."""
        fmt = OpenAIResponseFormatter()
        msgs = [
            Msg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="inner thoughts"),
                    TextBlock(type="text", text="reply"),
                ],
                role="assistant",
            ),
        ]
        res = await fmt.format(msgs)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["role"], "assistant")
        parts = res[0]["content"]
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["type"], "input_text")

    async def test_chat_formatter_thinking_echoed_with_reasoning_item_id(
        self,
    ) -> None:
        """ThinkingBlock with reasoning_item_id is echoed as a reasoning
        item."""
        fmt = OpenAIResponseFormatter()
        thinking = ThinkingBlock(thinking="my reasoning")
        thinking.reasoning_item_id = "rs_001"
        msgs = [
            Msg(
                name="assistant",
                content=[thinking, TextBlock(type="text", text="reply")],
                role="assistant",
            ),
        ]
        res = await fmt.format(msgs)
        reasoning_items = [r for r in res if r.get("type") == "reasoning"]
        self.assertEqual(len(reasoning_items), 1)
        self.assertEqual(reasoning_items[0]["id"], "rs_001")
        self.assertEqual(
            reasoning_items[0]["summary"],
            [{"type": "summary_text", "text": "my reasoning"}],
        )

    async def test_chat_formatter_url_image_in_tool_result(self) -> None:
        """URL images in tool results are promoted to a follow-up user
        message."""
        with patch.object(shortuuid, "uuid", return_value=_FIXED_ID):
            fmt = OpenAIResponseFormatter()
            msgs = [
                Msg(
                    name="assistant",
                    content=[
                        ToolCallBlock(
                            id="call_img",
                            name="get_map",
                            input='{"city": "Tokyo"}',
                        ),
                    ],
                    role="assistant",
                ),
                Msg(
                    name="tool",
                    content=[
                        ToolResultBlock(
                            id="call_img",
                            name="get_map",
                            output=[
                                TextBlock(
                                    type="text",
                                    text="Here is the map.",
                                ),
                                DataBlock(
                                    source=URLSource(
                                        url=self.image_url,
                                        media_type="image/png",
                                    ),
                                ),
                            ],
                            state=ToolResultState.SUCCESS,
                        ),
                    ],
                    role="assistant",
                ),
            ]
            res = await fmt.format(msgs)

        self.assertEqual(len(res), 3)
        # function_call
        self.assertEqual(res[0]["type"], "function_call")
        # function_call_output with reminder text
        self.assertEqual(res[1]["type"], "function_call_output")
        self.assertIn("Here is the map.", res[1]["output"])
        # promoted user message with the image
        self.assertEqual(res[2]["role"], "user")
        image_parts = [
            p
            for p in res[2]["content"]
            if p.get("type") == "input_image"
            and p.get("image_url") == self.image_url
        ]
        self.assertEqual(len(image_parts), 1)

    # -------------------------------------------------------------------
    # OpenAIResponseMultiAgentFormatter tests
    # -------------------------------------------------------------------

    async def test_multiagent_formatter(self) -> None:
        """MultiAgent formatter produces exact output for various subsets."""
        fmt = OpenAIResponseMultiAgentFormatter()
        self.maxDiff = None

        # Full history
        res = await fmt.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )
        self.assertListEqual(self.gt_multiagent, res)

        # Without system
        res = await fmt.format([*self.msgs_conversation, *self.msgs_tools])
        self.assertListEqual(self.gt_multiagent[1:], res)

        # Without tools
        res = await fmt.format([*self.msgs_system, *self.msgs_conversation])
        self.assertListEqual(self.gt_multiagent[:2], res)

        # System only
        res = await fmt.format(self.msgs_system)
        self.assertListEqual([self.gt_multiagent[0]], res)

        # Conversation only
        res = await fmt.format(self.msgs_conversation)
        self.assertListEqual([self.gt_multiagent[1]], res)

        # Tools only — is_first=True for trailing assistant
        res = await fmt.format(self.msgs_tools)
        self.assertListEqual(
            [
                self._gt_tool_call,
                self._gt_tool_result,
                self._gt_trailing_asst_first,
            ],
            res,
        )

        # System + tools — same is_first=True
        res = await fmt.format([*self.msgs_system, *self.msgs_tools])
        self.assertListEqual(
            [
                self.gt_multiagent[0],
                self._gt_tool_call,
                self._gt_tool_result,
                self._gt_trailing_asst_first,
            ],
            res,
        )

        # Empty
        res = await fmt.format([])
        self.assertListEqual([], res)
