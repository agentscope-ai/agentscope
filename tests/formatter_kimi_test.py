# -*- coding: utf-8 -*-
"""Comprehensive formatter unit tests for KimiChatFormatter and
KimiMultiAgentFormatter, following the reference test style with exact
ground-truth comparisons.
"""
from unittest import IsolatedAsyncioTestCase

from agentscope.formatter import (
    KimiChatFormatter,
    KimiMultiAgentFormatter,
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


class TestKimiFormatter(IsolatedAsyncioTestCase):
    """Comprehensive tests for Kimi Chat and MultiAgent formatters."""

    async def asyncSetUp(self) -> None:
        """Set up shared message fixtures and expected ground-truth dicts."""
        _img_src = URLSource(
            url="https://example.com/image.png",
            media_type="image/png",
        )
        image_url = str(_img_src.url)

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
                            url=image_url,
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
        # Ground truth: KimiChatFormatter
        #   - Same as OpenAI except ALL assistant messages have an extra
        #     "reasoning_content" field (empty string when no ThinkingBlock).
        # ---------------------------------------------------------------
        self.gt_chat = [
            {
                "role": "system",
                "name": "system",
                "content": [
                    {"type": "text", "text": "You're a helpful assistant."},
                ],
            },
            {
                "role": "user",
                "name": "user",
                "content": [
                    {"type": "text", "text": "What is the capital of France?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    },
                ],
            },
            {
                "role": "assistant",
                "name": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "The capital of France is Paris.",
                    },
                ],
                "reasoning_content": "",
            },
            {
                "role": "user",
                "name": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the capital of Germany?",
                    },
                ],
            },
            {
                "role": "assistant",
                "name": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "The capital of Germany is Berlin.",
                    },
                ],
                "reasoning_content": "",
            },
            {
                "role": "user",
                "name": "user",
                "content": [
                    {"type": "text", "text": "What is the capital of Japan?"},
                ],
            },
            {
                "role": "assistant",
                "name": "assistant",
                "content": None,
                "reasoning_content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_capital",
                            "arguments": '{"country": "Japan"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "The capital of Japan is Tokyo.",
                "name": "get_capital",
            },
            {
                "role": "assistant",
                "name": "assistant",
                "content": [
                    {"type": "text", "text": "The capital of Japan is Tokyo."},
                ],
                "reasoning_content": "",
            },
        ]

        # ---------------------------------------------------------------
        # Ground truth: KimiMultiAgentFormatter
        #   - Same as OpenAI MultiAgent, but tool-sequence assistant messages
        #     carry "reasoning_content": "".
        # ---------------------------------------------------------------
        _hist_prompt = KimiMultiAgentFormatter().conversation_history_prompt

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
                    "type": "text",
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
                    "type": "text",
                    "text": (
                        _hist_prompt + "<history>\n"
                        "assistant: The capital of Japan is Tokyo.\n"
                        "</history>"
                    ),
                },
            ],
        }

        self._gt_tool_call = {
            "role": "assistant",
            "name": "assistant",
            "content": None,
            "reasoning_content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_capital",
                        "arguments": '{"country": "Japan"}',
                    },
                },
            ],
        }
        self._gt_tool_result = {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "The capital of Japan is Tokyo.",
            "name": "get_capital",
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
                        "type": "text",
                        "text": (
                            _hist_prompt
                            + "<history>\n"
                            + _conv_text
                            + "\n</history>"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    },
                ],
            },
            self._gt_tool_call,
            self._gt_tool_result,
            _gt_trailing_asst_nonfirst,
        ]

    # -------------------------------------------------------------------
    # KimiChatFormatter tests
    # -------------------------------------------------------------------

    async def test_chat_formatter(self) -> None:
        """Chat formatter produces exact output for various subsets."""
        fmt = KimiChatFormatter()
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

    async def test_chat_formatter_thinking_to_reasoning_content(
        self,
    ) -> None:
        """ThinkingBlock becomes reasoning_content in Kimi (Preserved
        Thinking)."""
        fmt = KimiChatFormatter()
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
        self.assertEqual(res[0]["reasoning_content"], "inner thoughts")
        self.assertEqual(
            res[0]["content"],
            [{"type": "text", "text": "reply"}],
        )

    async def test_chat_formatter_assistant_always_has_reasoning_content(
        self,
    ) -> None:
        """All assistant messages always have reasoning_content (even when
        empty)."""
        fmt = KimiChatFormatter()
        msgs = [
            Msg(
                name="assistant",
                content="Hello!",
                role="assistant",
            ),
        ]
        res = await fmt.format(msgs)
        self.assertEqual(len(res), 1)
        self.assertIn("reasoning_content", res[0])
        self.assertEqual(res[0]["reasoning_content"], "")

    async def test_chat_formatter_base64_image(self) -> None:
        """Base64-encoded image is inlined as a data URI."""
        fmt = KimiChatFormatter()
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
                    "name": "user",
                    "content": [
                        {"type": "text", "text": "What's in this image?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": self.image_data_uri},
                        },
                    ],
                },
            ],
            res,
        )

    # -------------------------------------------------------------------
    # KimiMultiAgentFormatter tests
    # -------------------------------------------------------------------

    async def test_multiagent_formatter(self) -> None:
        """MultiAgent formatter produces exact output for various subsets."""
        fmt = KimiMultiAgentFormatter()
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
