# -*- coding: utf-8 -*-
"""Comprehensive formatter unit tests for DeepSeekChatFormatter and
DeepSeekMultiAgentFormatter, with exact ground-truth comparisons.
"""
from unittest import IsolatedAsyncioTestCase

from agentscope.formatter import (
    DeepSeekChatFormatter,
    DeepSeekMultiAgentFormatter,
)
from agentscope.message import (
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ThinkingBlock,
)
from agentscope.message._block import ToolResultState


class TestDeepSeekFormatter(IsolatedAsyncioTestCase):
    """Comprehensive tests for DeepSeek Chat and MultiAgent formatters."""

    async def asyncSetUp(self) -> None:
        """Set up shared fixtures and ground-truth dicts."""
        _hist_prompt = (
            DeepSeekMultiAgentFormatter().conversation_history_prompt
        )

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

        # --- Chat formatter ground truth ---
        # DeepSeek content is a plain string (not a list of blocks).
        # All assistant messages include `reasoning_content` (empty string if
        # no ThinkingBlock).
        self.gt_chat = [
            {"role": "system", "content": "You're a helpful assistant."},
            {"role": "user", "content": "What is the capital of France?"},
            {
                "role": "assistant",
                "content": "The capital of France is Paris.",
                "reasoning_content": "",
            },
            {"role": "user", "content": "What is the capital of Germany?"},
            {
                "role": "assistant",
                "content": "The capital of Germany is Berlin.",
                "reasoning_content": "",
            },
            {"role": "user", "content": "What is the capital of Japan?"},
            {
                "role": "assistant",
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
                "content": "The capital of Japan is Tokyo.",
                "reasoning_content": "",
            },
        ]

        # --- MultiAgent formatter ground truth ---
        # System content is a plain string.
        # History is a plain string (not a list) with <history> tags.
        # The trailing assistant message (is_first=False) is wrapped in a
        # minimal <history> block without the full prompt prefix.
        _gt_trailing_asst_nonfirst = {
            "role": "user",
            "content": (
                "<history>\n"
                "assistant: The capital of Japan is Tokyo.\n"
                "</history>"
            ),
        }
        self._gt_trailing_asst_first = {
            "role": "user",
            "content": (
                _hist_prompt + "<history>\n"
                "assistant: The capital of Japan is Tokyo.\n"
                "</history>"
            ),
        }
        self._gt_tool_call = {
            "role": "assistant",
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
            {"role": "system", "content": "You're a helpful assistant."},
            {
                "role": "user",
                "content": (
                    _hist_prompt + "<history>\n"
                    "user: What is the capital of France?\n"
                    "assistant: The capital of France is Paris.\n"
                    "user: What is the capital of Germany?\n"
                    "assistant: The capital of Germany is Berlin.\n"
                    "user: What is the capital of Japan?\n"
                    "</history>"
                ),
            },
            self._gt_tool_call,
            self._gt_tool_result,
            _gt_trailing_asst_nonfirst,
        ]

    # ------------------------------------------------------------------
    # DeepSeekChatFormatter tests
    # ------------------------------------------------------------------

    async def test_chat_formatter(self) -> None:
        """Chat formatter produces exact output for various subsets."""
        fmt = DeepSeekChatFormatter()
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
        self.assertListEqual([], await fmt.format([]))

    async def test_chat_formatter_reasoning_content_always_present(
        self,
    ) -> None:
        """Every assistant message always has a reasoning_content field."""
        fmt = DeepSeekChatFormatter()
        msgs = [Msg(name="assistant", content="Answer", role="assistant")]
        res = await fmt.format(msgs)
        self.assertIn("reasoning_content", res[0])
        self.assertEqual(res[0]["reasoning_content"], "")

    async def test_chat_formatter_thinking_block(self) -> None:
        """ThinkingBlock is placed into reasoning_content."""
        fmt = DeepSeekChatFormatter()
        msgs = [
            Msg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="Let me think..."),
                    TextBlock(type="text", text="Answer"),
                ],
                role="assistant",
            ),
        ]
        res = await fmt.format(msgs)
        self.assertEqual(res[0]["reasoning_content"], "Let me think...")
        self.assertEqual(res[0]["content"], "Answer")

    # ------------------------------------------------------------------
    # DeepSeekMultiAgentFormatter tests
    # ------------------------------------------------------------------

    async def test_multiagent_formatter(self) -> None:
        """MultiAgent formatter produces exact output for various subsets."""
        fmt = DeepSeekMultiAgentFormatter()
        self.maxDiff = None

        # Full
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

        # Tools only (is_first=True for trailing assistant)
        res = await fmt.format(self.msgs_tools)
        self.assertListEqual(
            [
                self._gt_tool_call,
                self._gt_tool_result,
                self._gt_trailing_asst_first,
            ],
            res,
        )

        # System + tools (is_first=True for trailing assistant)
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
        self.assertListEqual([], await fmt.format([]))
