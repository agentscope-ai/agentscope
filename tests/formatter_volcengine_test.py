# -*- coding: utf-8 -*-
"""Comprehensive formatter unit tests for VolcengineChatFormatter and
VolcengineMultiAgentFormatter, with exact ground-truth comparisons.
"""

from unittest import IsolatedAsyncioTestCase

from agentscope.formatter import (
    VolcengineChatFormatter,
    VolcengineMultiAgentFormatter,
)
from agentscope.message import (
    UserMsg,
    AssistantMsg,
    SystemMsg,
    TextBlock,
    DataBlock,
    Base64Source,
    URLSource,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    ThinkingBlock,
    HintBlock,
)


class TestVolcengineFormatter(IsolatedAsyncioTestCase):
    """Comprehensive tests for Volcengine Chat and MultiAgent formatters."""

    async def asyncSetUp(self) -> None:
        """Set up shared fixtures and ground-truth dicts."""
        _hist_prompt = (
            VolcengineMultiAgentFormatter().conversation_history_prompt
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
        # Text content is a list of blocks, as in the OpenAI formatter.
        # Ark only needs `reasoning_content` when preserving a ThinkingBlock.
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
                    {"type": "text", "text": "What is the capital of France?"},
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
                    {"type": "text", "text": "What is the capital of Japan?"},
                ],
            },
            {
                "role": "assistant",
                "content": None,
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
                "content": [
                    {"type": "text", "text": "The capital of Japan is Tokyo."},
                ],
            },
        ]

        # --- MultiAgent formatter ground truth ---
        # System content is a plain string.
        # History is a single text block with <history> tags.
        # The trailing assistant message (is_first=False) is wrapped in a
        # minimal <history> block without the full prompt prefix.
        self._gt_trailing_asst = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "The capital of Japan is Tokyo."},
            ],
        }
        self._gt_tool_call = {
            "role": "assistant",
            "content": None,
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
                "content": [
                    {
                        "type": "text",
                        "text": (
                            _hist_prompt + "<history>\n"
                            "user: What is the capital of France?\n"
                            "assistant: The capital of France is Paris.\n"
                            "user: What is the capital of Germany?\n"
                            "assistant: The capital of Germany is Berlin.\n"
                            "user: What is the capital of Japan?\n"
                            "</history>"
                        ),
                    },
                ],
            },
            self._gt_tool_call,
            self._gt_tool_result,
            self._gt_trailing_asst,
        ]

    # ------------------------------------------------------------------
    # VolcengineChatFormatter tests
    # ------------------------------------------------------------------

    async def test_chat_formatter(self) -> None:
        """Chat formatter produces exact output for various subsets."""
        fmt = VolcengineChatFormatter()

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

    async def test_chat_formatter_omits_empty_reasoning_content(
        self,
    ) -> None:
        """A non-thinking assistant message omits reasoning_content."""
        fmt = VolcengineChatFormatter()
        msgs = [AssistantMsg(name="assistant", content="Answer")]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Answer"}],
                },
            ],
            res,
        )

    async def test_chat_formatter_thinking_block(self) -> None:
        """ThinkingBlock is placed into reasoning_content."""
        fmt = VolcengineChatFormatter()
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
                    "content": [{"type": "text", "text": "Answer"}],
                    "reasoning_content": "Let me think...",
                },
            ],
            res,
        )

    # ------------------------------------------------------------------
    # VolcengineMultiAgentFormatter tests
    # ------------------------------------------------------------------

    async def test_multiagent_formatter(self) -> None:
        """MultiAgent formatter produces exact output for various subsets."""
        fmt = VolcengineMultiAgentFormatter()

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

    async def test_chat_formatter_complex_multi_step(self) -> None:
        """Complex multi-step sequence with interleaved thinking, text,
        tool calls, and tool results."""
        fmt = VolcengineChatFormatter()
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
                    "content": [{"type": "text", "text": "text_1"}],
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
                    "content": [{"type": "text", "text": "text_2"}],
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
                    "content": [{"type": "text", "text": "text_3"}],
                    "reasoning_content": "thinking_3",
                },
            ],
            res,
        )

    async def test_chat_formatter_hint_block(self) -> None:
        """HintBlock flushes preceding content and becomes a user message."""
        fmt = VolcengineChatFormatter()
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
                    "content": [
                        {"type": "text", "text": "Let me think about that."},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Remember to be concise."},
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Here is my answer."},
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_hint_block_multimodal(self) -> None:
        """HintBlock preserves supported image data."""
        fmt = VolcengineChatFormatter()
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
        res = await fmt.format(msgs)
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
            res,
        )

    async def test_chat_formatter_multimodal_url_and_base64(self) -> None:
        """Ark image/video input accepts URLs and base64 data URIs."""
        fmt = VolcengineChatFormatter()
        msgs = [
            UserMsg(
                name="user",
                content=[
                    TextBlock(text="Compare these images."),
                    DataBlock(
                        source=URLSource(
                            url="https://example.com/first.png",
                            media_type="image/png",
                        ),
                    ),
                    DataBlock(
                        source=Base64Source(
                            data="ZmFrZQ==",
                            media_type="image/jpeg",
                        ),
                    ),
                    DataBlock(
                        source=URLSource(
                            url="https://example.com/video.mp4",
                            media_type="video/mp4",
                        ),
                    ),
                    DataBlock(
                        source=Base64Source(
                            data="dmlkZW8=",
                            media_type="video/quicktime",
                        ),
                    ),
                ],
            ),
        ]

        self.assertListEqual(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Compare these images."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "https://example.com/first.png",
                            },
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/jpeg;base64,ZmFrZQ==",
                            },
                        },
                        {
                            "type": "video_url",
                            "video_url": {
                                "url": "https://example.com/video.mp4",
                            },
                        },
                        {
                            "type": "video_url",
                            "video_url": {
                                "url": "data:video/quicktime;base64,dmlkZW8=",
                            },
                        },
                    ],
                },
            ],
            await fmt.format(msgs),
        )

    async def test_multiagent_formatter_preserves_images(self) -> None:
        """Multi-agent history should retain supported image blocks."""
        fmt = VolcengineMultiAgentFormatter()
        msgs = [
            UserMsg(
                name="alice",
                content=[
                    TextBlock(text="Describe this image."),
                    DataBlock(
                        source=URLSource(
                            url="https://example.com/image.png",
                            media_type="image/png",
                        ),
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
                            "text": (
                                fmt.conversation_history_prompt
                                + "<history>\nalice: Describe this image.\n"
                                "</history>"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "https://example.com/image.png",
                            },
                        },
                    ],
                },
            ],
            await fmt.format(msgs),
        )
