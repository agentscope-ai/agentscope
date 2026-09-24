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
    UserMsg,
    AssistantMsg,
    SystemMsg,
    TextBlock,
    DataBlock,
    Base64Source,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    ThinkingBlock,
    HintBlock,
)


_FIXED_ID = "TESTID1234567"


class TestDeepSeekFormatter(IsolatedAsyncioTestCase):
    """Comprehensive tests for DeepSeek Chat and MultiAgent formatters."""

    async def asyncSetUp(self) -> None:
        """Set up shared fixtures and ground-truth dicts."""
        _hist_prompt = (
            DeepSeekMultiAgentFormatter().conversation_history_prompt
        )

        self.msgs_system = [
            SystemMsg(
                name="system",
                content="You're a helpful assistant.",
            ),
        ]
        self.msgs_conversation = [
            UserMsg(
                name="user",
                content="What is the capital of France?",
            ),
            AssistantMsg(
                name="assistant",
                content="The capital of France is Paris.",
            ),
            UserMsg(
                name="user",
                content="What is the capital of Germany?",
            ),
            AssistantMsg(
                name="assistant",
                content="The capital of Germany is Berlin.",
            ),
            UserMsg(
                name="user",
                content="What is the capital of Japan?",
            ),
        ]
        self.msgs_tools = [
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call_1",
                        name="get_capital",
                        input='{"country": "Japan"}',
                    ),
                    ToolResultBlock(
                        id="call_1",
                        name="get_capital",
                        output=[
                            TextBlock(text="The capital of Japan is Tokyo."),
                        ],
                        state=ToolResultState.SUCCESS,
                    ),
                    TextBlock(text="The capital of Japan is Tokyo."),
                ],
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
        self._gt_trailing_asst = {
            "role": "assistant",
            "content": "The capital of Japan is Tokyo.",
            "reasoning_content": "",
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
            self._gt_trailing_asst,
        ]

    # ------------------------------------------------------------------
    # DeepSeekChatFormatter tests
    # ------------------------------------------------------------------

    async def test_chat_formatter(self) -> None:
        """Chat formatter produces exact output for various subsets."""
        fmt = DeepSeekChatFormatter()

        # Full history
        res = await fmt.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )
        self.assertListEqual(self.gt_chat, res)

        # Without system
        res = await fmt.format([*self.msgs_conversation, *self.msgs_tools])
        self.assertListEqual(self.gt_chat[1:], res)

        # Without conversation
        n_tools_gt = len(self.gt_chat) - 1 - len(self.msgs_conversation)
        res = await fmt.format([*self.msgs_system, *self.msgs_tools])
        self.assertListEqual(
            [self.gt_chat[0]] + self.gt_chat[-n_tools_gt:],
            res,
        )

        # Without tools
        res = await fmt.format([*self.msgs_system, *self.msgs_conversation])
        self.assertListEqual(self.gt_chat[:-n_tools_gt], res)

        # Empty
        self.assertListEqual([], await fmt.format([]))

    async def test_chat_formatter_preserves_supported_images(self) -> None:
        """Supported user images are preserved with DeepSeek boundaries."""
        fmt = DeepSeekChatFormatter()
        image = DataBlock(
            source=Base64Source(
                data="ZmFrZQ==",
                media_type="image/png",
            ),
        )
        msgs = [
            UserMsg(
                name="user",
                content=[
                    TextBlock(text="Inspect this image."),
                    image,
                ],
            ),
            UserMsg(name="user", content=[image]),
        ]

        self.assertListEqual(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Inspect this image."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,ZmFrZQ==",
                            },
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,ZmFrZQ==",
                            },
                        },
                    ],
                },
            ],
            await fmt.format(msgs),
        )
        with self.assertLogs("as", level="WARNING") as log_context:
            self.assertListEqual(
                [],
                await fmt.format(
                    [AssistantMsg(name="assistant", content=[image])],
                ),
            )
        self.assertListEqual(
            [
                "WARNING:as:DataBlock in assistant role is not supported "
                "by DeepSeek API, skipped.",
            ],
            log_context.output,
        )
        self.assertListEqual(
            [],
            await fmt.format(
                [
                    UserMsg(
                        name="user",
                        content=[
                            DataBlock(
                                source=Base64Source(
                                    data="ZmFrZQ==",
                                    media_type="image/svg+xml",
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        )

    async def test_chat_formatter_reasoning_content_always_present(
        self,
    ) -> None:
        """Every assistant message always has a reasoning_content field."""
        fmt = DeepSeekChatFormatter()
        msgs = [AssistantMsg(name="assistant", content="Answer")]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": "Answer",
                    "reasoning_content": "",
                },
            ],
            res,
        )

    async def test_chat_formatter_thinking_block(self) -> None:
        """ThinkingBlock is placed into reasoning_content."""
        fmt = DeepSeekChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="Let me think..."),
                    TextBlock(text="Answer"),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": "Answer",
                    "reasoning_content": "Let me think...",
                },
            ],
            res,
        )

    # ------------------------------------------------------------------
    # DeepSeekMultiAgentFormatter tests
    # ------------------------------------------------------------------

    async def test_multiagent_formatter(self) -> None:
        """MultiAgent formatter produces exact output for various subsets."""
        fmt = DeepSeekMultiAgentFormatter()

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

        # Tools only
        res = await fmt.format(self.msgs_tools)
        self.assertListEqual(
            [
                self._gt_tool_call,
                self._gt_tool_result,
                self._gt_trailing_asst,
            ],
            res,
        )

        # System + tools
        res = await fmt.format([*self.msgs_system, *self.msgs_tools])
        self.assertListEqual(
            [
                self.gt_multiagent[0],
                self._gt_tool_call,
                self._gt_tool_result,
                self._gt_trailing_asst,
            ],
            res,
        )

        # Empty
        self.assertListEqual([], await fmt.format([]))

    async def test_multiagent_formatter_preserves_all_agent_images(
        self,
    ) -> None:
        """Collapsed history preserves images regardless of source role."""
        fmt = DeepSeekMultiAgentFormatter(
            input_types=["text/plain", "image/png"],
        )
        msgs = [
            UserMsg(name="user", content="Inspect the result."),
            AssistantMsg(
                name="analyst",
                content=[
                    TextBlock(text="Here is the screenshot."),
                    DataBlock(
                        source=Base64Source(
                            data="ZmFrZQ==",
                            media_type="image/png",
                        ),
                    ),
                ],
            ),
        ]
        prompt = fmt.conversation_history_prompt

        self.assertListEqual(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"{prompt}<history>\n"
                                "user: Inspect the result.\n"
                                "analyst: Here is the screenshot.\n"
                                "analyst: "
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,ZmFrZQ==",
                            },
                        },
                        {"type": "text", "text": "\n</history>"},
                    ],
                },
            ],
            await fmt.format(msgs),
        )

    async def test_chat_formatter_complex_multi_step(self) -> None:
        """Complex multi-step sequence with interleaved thinking, text,
        tool calls, and tool results."""
        fmt = DeepSeekChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="thinking_1"),
                    TextBlock(text="text_1"),
                    ToolCallBlock(
                        id="call_1",
                        name="func_1",
                        input='{"arg": "value1"}',
                    ),
                    ToolCallBlock(
                        id="call_2",
                        name="func_2",
                        input='{"arg": "value2"}',
                    ),
                    ToolResultBlock(
                        id="call_1",
                        name="func_1",
                        output=[TextBlock(text="result_1")],
                        state=ToolResultState.SUCCESS,
                    ),
                    ToolResultBlock(
                        id="call_2",
                        name="func_2",
                        output=[TextBlock(text="result_2")],
                        state=ToolResultState.SUCCESS,
                    ),
                    ThinkingBlock(thinking="thinking_2"),
                    TextBlock(text="text_2"),
                    ToolCallBlock(
                        id="call_3",
                        name="func_3",
                        input='{"arg": "value3"}',
                    ),
                    ToolResultBlock(
                        id="call_3",
                        name="func_3",
                        output=[TextBlock(text="result_3")],
                        state=ToolResultState.SUCCESS,
                    ),
                    ToolCallBlock(
                        id="call_4",
                        name="func_4",
                        input='{"arg": "value4"}',
                    ),
                    ToolResultBlock(
                        id="call_4",
                        name="func_4",
                        output=[TextBlock(text="result_4")],
                        state=ToolResultState.SUCCESS,
                    ),
                    ThinkingBlock(thinking="thinking_3"),
                    TextBlock(text="text_3"),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": "text_1",
                    "reasoning_content": "thinking_1",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "func_1",
                                "arguments": '{"arg": "value1"}',
                            },
                        },
                        {
                            "id": "call_2",
                            "type": "function",
                            "function": {
                                "name": "func_2",
                                "arguments": '{"arg": "value2"}',
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": "result_1",
                    "name": "func_1",
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_2",
                    "content": "result_2",
                    "name": "func_2",
                },
                {
                    "role": "assistant",
                    "content": "text_2",
                    "reasoning_content": "thinking_2",
                    "tool_calls": [
                        {
                            "id": "call_3",
                            "type": "function",
                            "function": {
                                "name": "func_3",
                                "arguments": '{"arg": "value3"}',
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_3",
                    "content": "result_3",
                    "name": "func_3",
                },
                {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "",
                    "tool_calls": [
                        {
                            "id": "call_4",
                            "type": "function",
                            "function": {
                                "name": "func_4",
                                "arguments": '{"arg": "value4"}',
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_4",
                    "content": "result_4",
                    "name": "func_4",
                },
                {
                    "role": "assistant",
                    "content": "text_3",
                    "reasoning_content": "thinking_3",
                },
            ],
            res,
        )

    async def test_chat_formatter_hint_block(self) -> None:
        """HintBlock flushes preceding content and becomes a user message."""
        fmt = DeepSeekChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    TextBlock(text="Let me think about that."),
                    HintBlock(hint="Remember to be concise."),
                    TextBlock(text="Here is my answer."),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": "Let me think about that.",
                    "reasoning_content": "",
                },
                {
                    "role": "user",
                    "content": "Remember to be concise.",
                },
                {
                    "role": "assistant",
                    "content": "Here is my answer.",
                    "reasoning_content": "",
                },
            ],
            res,
        )

    async def test_chat_formatter_promotes_tool_result_image(self) -> None:
        """Tool-result images are promoted after the tool message."""
        fmt = DeepSeekChatFormatter(
            input_types=["text/plain", "image/png"],
        )
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call_image",
                        name="capture",
                        input="{}",
                    ),
                    ToolResultBlock(
                        id="call_image",
                        name="capture",
                        output=[
                            TextBlock(text="Screenshot captured."),
                            DataBlock(
                                id=_FIXED_ID,
                                source=Base64Source(
                                    data="ZmFrZQ==",
                                    media_type="image/png",
                                ),
                            ),
                        ],
                        state=ToolResultState.SUCCESS,
                    ),
                    TextBlock(text="I can inspect it now."),
                ],
            ),
        ]
        tool_content = (
            "Screenshot captured.\n"
            "<system-reminder>A(n) image file is returned and will be "
            "presented to you with the identifier "
            f"[{_FIXED_ID}].</system-reminder>"
        )

        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "",
                    "tool_calls": [
                        {
                            "id": "call_image",
                            "type": "function",
                            "function": {
                                "name": "capture",
                                "arguments": "{}",
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_image",
                    "content": tool_content,
                    "name": "capture",
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "<system-reminder>The multimodal data and "
                                "their identifiers are listed as follows:"
                            ),
                        },
                        {
                            "type": "text",
                            "text": f"- {_FIXED_ID} (image file): ",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,ZmFrZQ==",
                            },
                        },
                        {
                            "type": "text",
                            "text": "</system-reminder>",
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": "I can inspect it now.",
                    "reasoning_content": "",
                },
            ],
            await fmt.format(msgs),
        )

    async def test_chat_formatter_hint_block_multimodal(self) -> None:
        """A supported image in a hint is sent in a user message."""
        fmt = DeepSeekChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    HintBlock(
                        hint=[
                            TextBlock(text="Inspect this screenshot:"),
                            DataBlock(
                                source=Base64Source(
                                    data="ZmFrZSBpbWFnZSBkYXRh",
                                    media_type="image/png",
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        ]
        self.assertListEqual(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Inspect this screenshot:",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    "data:image/png;base64,"
                                    "ZmFrZSBpbWFnZSBkYXRh"
                                ),
                            },
                        },
                    ],
                },
            ],
            await fmt.format(msgs),
        )
