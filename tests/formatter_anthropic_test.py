# -*- coding: utf-8 -*-
"""Comprehensive formatter unit tests for AnthropicChatFormatter and
AnthropicMultiAgentFormatter, following the reference test style with exact
ground-truth comparisons.
"""
from unittest import IsolatedAsyncioTestCase

from agentscope.formatter import (
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
)
from agentscope.message import (
    Msg,
    TextBlock,
    DataBlock,
    ToolCallBlock,
    ToolResultBlock,
    Base64Source,
    ThinkingBlock,
)
from agentscope.message._block import ToolResultState


class TestAnthropicFormatter(IsolatedAsyncioTestCase):
    """Comprehensive tests for Anthropic Chat and MultiAgent formatters."""

    async def asyncSetUp(self) -> None:
        """Set up shared message fixtures and expected ground-truth dicts."""
        self.image_b64 = "ZmFrZSBpbWFnZSBkYXRh"

        # ---------------------------------------------------------------
        # Message fixtures
        # (No URL images: Anthropic URL handling downloads from the network)
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
                content="What is the capital of France?",
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
        # Ground truth: AnthropicChatFormatter
        #   - No "name" field.
        #   - Content is always a list of {"type": ..., ...} dicts.
        #   - ToolResultBlock forces role to "user".
        #   - ToolCallBlock "input" is a dict (parsed from JSON string).
        # ---------------------------------------------------------------
        self.gt_chat = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "You're a helpful assistant."},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the capital of France?",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "The capital of France is Paris.",
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the capital of Germany?",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "The capital of Germany is Berlin.",
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the capital of Japan?",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "get_capital",
                        "input": {"country": "Japan"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": [
                            {
                                "type": "text",
                                "text": "The capital of Japan is Tokyo.",
                            },
                        ],
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "The capital of Japan is Tokyo.",
                    },
                ],
            },
        ]

        # ---------------------------------------------------------------
        # Ground truth: AnthropicMultiAgentFormatter
        #   - System: {"role": "system", "content": [{"type": "text", ...}]}
        #   - Agent messages (is_first=True): wrapped in hist_prompt +
        #     <history>...</history>.
        #   - Agent messages (is_first=False): no wrapping at all.
        # ---------------------------------------------------------------
        _hist_prompt = (
            AnthropicMultiAgentFormatter().conversation_history_prompt
        )

        _conv_text = (
            "user: What is the capital of France?\n"
            "assistant: The capital of France is Paris.\n"
            "user: What is the capital of Germany?\n"
            "assistant: The capital of Germany is Berlin.\n"
            "user: What is the capital of Japan?"
        )

        # is_first=False: no wrapping (Anthropic only wraps on first group)
        _gt_trailing_asst_nonfirst = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "assistant: The capital of Japan is Tokyo.",
                },
            ],
        }
        # is_first=True: full wrapping with hist_prompt
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
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "get_capital",
                    "input": {"country": "Japan"},
                },
            ],
        }
        self._gt_tool_result = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": [
                        {
                            "type": "text",
                            "text": "The capital of Japan is Tokyo.",
                        },
                    ],
                },
            ],
        }

        self.gt_multiagent = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "You're a helpful assistant."},
                ],
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
                ],
            },
            self._gt_tool_call,
            self._gt_tool_result,
            _gt_trailing_asst_nonfirst,
        ]

    # -------------------------------------------------------------------
    # AnthropicChatFormatter tests
    # -------------------------------------------------------------------

    async def test_chat_formatter(self) -> None:
        """Chat formatter produces exact output for various subsets."""
        fmt = AnthropicChatFormatter()
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
        """Base64-encoded image is formatted as Anthropic image source."""
        fmt = AnthropicChatFormatter()
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
                        {"type": "text", "text": "What's in this image?"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": self.image_b64,
                            },
                        },
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_thinking_preserved(self) -> None:
        """ThinkingBlock is passed back as a thinking content block."""
        fmt = AnthropicChatFormatter()
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
        thinking_blocks = [
            b for b in res[0]["content"] if b.get("type") == "thinking"
        ]
        self.assertEqual(len(thinking_blocks), 1)
        self.assertEqual(thinking_blocks[0]["thinking"], "inner thoughts")
        self.assertEqual(thinking_blocks[0]["signature"], "")

    async def test_chat_formatter_tool_result_role_forced_to_user(
        self,
    ) -> None:
        """Anthropic forces tool_result messages to role='user'."""
        fmt = AnthropicChatFormatter()
        res = await fmt.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )
        tool_results = [
            m
            for m in res
            if any(
                b.get("type") == "tool_result"
                for b in (m.get("content") or [])
            )
        ]
        self.assertTrue(len(tool_results) > 0)
        for tr in tool_results:
            self.assertEqual(tr["role"], "user")

    async def test_chat_formatter_tool_result_with_image(self) -> None:
        """Tool result containing an image DataBlock inlines the image in the
        tool_result content without crashing on TextBlock system-reminders."""
        fmt = AnthropicChatFormatter()
        msgs = [
            Msg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call_img",
                        name="get_chart",
                        input="{}",
                    ),
                ],
                role="assistant",
            ),
            Msg(
                name="tool",
                content=[
                    ToolResultBlock(
                        id="call_img",
                        name="get_chart",
                        output=[
                            TextBlock(type="text", text="Here is the chart."),
                            DataBlock(
                                source=Base64Source(
                                    data=self.image_b64,
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
        # There should be 2 messages: the tool_call and the tool_result
        self.assertEqual(len(res), 2)
        # Find the tool_result message
        tool_result_msg = next(
            m
            for m in res
            if any(
                b.get("type") == "tool_result"
                for b in (m.get("content") or [])
            )
        )
        self.assertEqual(tool_result_msg["role"], "user")
        tr_content = next(
            b
            for b in tool_result_msg["content"]
            if b.get("type") == "tool_result"
        )["content"]
        # The content should have a text part and an image part
        types = [b.get("type") for b in tr_content]
        self.assertIn("text", types)
        self.assertIn("image", types)

    # -------------------------------------------------------------------
    # AnthropicMultiAgentFormatter tests
    # -------------------------------------------------------------------

    async def test_multiagent_formatter(self) -> None:
        """MultiAgent formatter produces exact output for various subsets."""
        fmt = AnthropicMultiAgentFormatter()
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

        # System + tools (no conversation) — same is_first=True
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
