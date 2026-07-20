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
    UserMsg,
    AssistantMsg,
    SystemMsg,
    TextBlock,
    DataBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    Base64Source,
    ThinkingBlock,
    HintBlock,
)


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

        self._gt_trailing_asst = {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "The capital of Japan is Tokyo.",
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
            self._gt_trailing_asst,
        ]

    # -------------------------------------------------------------------
    # AnthropicChatFormatter tests
    # -------------------------------------------------------------------

    async def test_chat_formatter(self) -> None:
        """Chat formatter produces exact output for various subsets."""
        fmt = AnthropicChatFormatter()

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
        res = await fmt.format([])
        self.assertListEqual([], res)

    async def test_chat_formatter_base64_image(self) -> None:
        """Base64-encoded image is formatted as Anthropic image source."""
        fmt = AnthropicChatFormatter()
        msgs = [
            UserMsg(
                name="user",
                content=[
                    TextBlock(text="What's in this image?"),
                    DataBlock(
                        source=Base64Source(
                            data=self.image_b64,
                            media_type="image/png",
                        ),
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
        """ThinkingBlock with a signature is passed back as a thinking
        content block."""
        fmt = AnthropicChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ThinkingBlock(
                        thinking="inner thoughts",
                        signature="sig_abc",
                    ),
                    TextBlock(text="reply"),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "inner thoughts",
                            "signature": "sig_abc",
                        },
                        {"type": "text", "text": "reply"},
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_thinking_without_signature_dropped(
        self,
    ) -> None:
        """ThinkingBlock from another provider (no signature) is dropped
        rather than forwarded with an empty signature, which Anthropic
        rejects with `Invalid signature in thinking block`. An empty-string
        signature is treated the same as a missing one."""
        fmt = AnthropicChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="from another provider"),
                    ThinkingBlock(thinking="empty sig", signature=""),
                    TextBlock(text="reply"),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "reply"},
                    ],
                },
            ],
            res,
        )

        # Thinking-only message produces no output (no empty assistant msg).
        res = await fmt.format(
            [
                AssistantMsg(
                    name="assistant",
                    content=[ThinkingBlock(thinking="only thinking")],
                ),
            ],
        )
        self.assertListEqual([], res)

    async def test_chat_formatter_tool_result_role_forced_to_user(
        self,
    ) -> None:
        """Anthropic forces tool_result messages to role='user'."""
        fmt = AnthropicChatFormatter()
        res = await fmt.format(
            [*self.msgs_system, *self.msgs_conversation, *self.msgs_tools],
        )
        tool_result_roles = [
            m["role"]
            for m in res
            if any(
                b.get("type") == "tool_result"
                for b in (m.get("content") or [])
            )
        ]
        self.assertListEqual(tool_result_roles, ["user"])

    async def test_chat_formatter_tool_result_with_image(self) -> None:
        """Tool result containing an image DataBlock inlines the image in the
        tool_result content without crashing on TextBlock system-reminders."""
        fmt = AnthropicChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call_img",
                        name="get_chart",
                        input="{}",
                    ),
                    ToolResultBlock(
                        id="call_img",
                        name="get_chart",
                        output=[
                            TextBlock(text="Here is the chart."),
                            DataBlock(
                                source=Base64Source(
                                    data=self.image_b64,
                                    media_type="image/png",
                                ),
                            ),
                        ],
                        state=ToolResultState.SUCCESS,
                    ),
                    TextBlock(text="Here is the chart analysis."),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_img",
                            "name": "get_chart",
                            "input": {},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_img",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Here is the chart.",
                                },
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
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "Here is the chart analysis.",
                        },
                    ],
                },
            ],
            res,
        )

    # -------------------------------------------------------------------
    # AnthropicMultiAgentFormatter tests
    # -------------------------------------------------------------------

    async def test_multiagent_formatter(self) -> None:
        """MultiAgent formatter produces exact output for various subsets."""
        fmt = AnthropicMultiAgentFormatter()

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

        # System + tools (no conversation)
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
        res = await fmt.format([])
        self.assertListEqual([], res)

    async def test_chat_formatter_complex_multi_step(self) -> None:
        """Complex multi-step sequence with interleaved thinking, text,
        tool calls, and tool results."""
        fmt = AnthropicChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="thinking_1", signature="sig_1"),
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
                    ThinkingBlock(thinking="thinking_2", signature="sig_2"),
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
                    ThinkingBlock(thinking="thinking_3", signature="sig_3"),
                    TextBlock(text="text_3"),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "thinking_1",
                            "signature": "sig_1",
                        },
                        {"type": "text", "text": "text_1"},
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "func_1",
                            "input": {"arg": "value1"},
                        },
                        {
                            "type": "tool_use",
                            "id": "call_2",
                            "name": "func_2",
                            "input": {"arg": "value2"},
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
                                {"type": "text", "text": "result_1"},
                            ],
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_2",
                            "content": [
                                {"type": "text", "text": "result_2"},
                            ],
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "thinking_2",
                            "signature": "sig_2",
                        },
                        {"type": "text", "text": "text_2"},
                        {
                            "type": "tool_use",
                            "id": "call_3",
                            "name": "func_3",
                            "input": {"arg": "value3"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_3",
                            "content": [
                                {"type": "text", "text": "result_3"},
                            ],
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_4",
                            "name": "func_4",
                            "input": {"arg": "value4"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_4",
                            "content": [
                                {"type": "text", "text": "result_4"},
                            ],
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "thinking_3",
                            "signature": "sig_3",
                        },
                        {"type": "text", "text": "text_3"},
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_hint_block(self) -> None:
        """HintBlock flushes preceding content and becomes a user message."""
        fmt = AnthropicChatFormatter()
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
        """Multimodal HintBlock becomes a single user message with text
        + image."""
        fmt = AnthropicChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    HintBlock(
                        hint=[
                            TextBlock(text="Inspect this screenshot:"),
                            DataBlock(
                                source=Base64Source(
                                    data=self.image_b64,
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

    async def test_chat_formatter_parallel_tool_results_merged(
        self,
    ) -> None:
        """Parallel tool_results must be grouped into ONE user message.

        Regression test for Issue #1892: AnthropicChatFormatter was flushing
        content_blocks on every ToolResultBlock, which split N parallel results
        into N separate user messages. Strict endpoints such as DeepSeek's
        Anthropic-compatible API reject this with HTTP 400.
        """
        fmt = AnthropicChatFormatter()
        msgs = [
            UserMsg(
                name="user",
                content="Get weather for Beijing, Shanghai, and Guangzhou.",
            ),
            AssistantMsg(
                name="assistant",
                content=[
                    TextBlock(text="Let me check all three cities."),
                    ToolCallBlock(
                        id="call_01",
                        name="get_weather",
                        input='{"city": "Beijing"}',
                    ),
                    ToolCallBlock(
                        id="call_02",
                        name="get_weather",
                        input='{"city": "Shanghai"}',
                    ),
                    ToolCallBlock(
                        id="call_03",
                        name="get_weather",
                        input='{"city": "Guangzhou"}',
                    ),
                    ToolResultBlock(
                        id="call_01",
                        name="get_weather",
                        output="Sunny, 28\u00b0C",
                        state=ToolResultState.SUCCESS,
                    ),
                    ToolResultBlock(
                        id="call_02",
                        name="get_weather",
                        output="Cloudy, 24\u00b0C",
                        state=ToolResultState.SUCCESS,
                    ),
                    ToolResultBlock(
                        id="call_03",
                        name="get_weather",
                        output="Rainy, 22\u00b0C",
                        state=ToolResultState.SUCCESS,
                    ),
                ],
            ),
        ]
        res = await fmt.format(msgs)

        # Expected: 3 messages total
        #   [0] user  -> original question
        #   [1] assistant -> text + 3x tool_use
        #   [2] user  -> ALL 3 tool_results in ONE message  (the fix)
        self.assertListEqual(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Get weather for Beijing, Shanghai, "
                            "and Guangzhou.",
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "Let me check all three cities.",
                        },
                        {
                            "type": "tool_use",
                            "id": "call_01",
                            "name": "get_weather",
                            "input": {"city": "Beijing"},
                        },
                        {
                            "type": "tool_use",
                            "id": "call_02",
                            "name": "get_weather",
                            "input": {"city": "Shanghai"},
                        },
                        {
                            "type": "tool_use",
                            "id": "call_03",
                            "name": "get_weather",
                            "input": {"city": "Guangzhou"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_01",
                            "content": [
                                {"type": "text", "text": "Sunny, 28\u00b0C"},
                            ],
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_02",
                            "content": [
                                {"type": "text", "text": "Cloudy, 24\u00b0C"},
                            ],
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_03",
                            "content": [
                                {"type": "text", "text": "Rainy, 22\u00b0C"},
                            ],
                        },
                    ],
                },
            ],
            res,
        )

    async def test_tool_call_with_incomplete_input_does_not_crash(
        self,
    ) -> None:
        """A truncated ``block.input`` must not crash the formatter.

        Context compression or interrupted streaming can leave a
        ``ToolCallBlock.input`` as an incomplete JSON fragment (e.g.
        ``'{"key'``). The formatter must repair it into a dict (here
        ``{}``) instead of raising ``JSONDecodeError``.
        """
        fmt = AnthropicChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call_1",
                        name="get_weather",
                        input='{"city": "Tok',
                    ),
                ],
            ),
        ]

        res = await fmt.format(msgs)

        tool_use = [
            b for b in res[0]["content"] if b.get("type") == "tool_use"
        ][0]
        # Repaired to a dict rather than crashing.
        self.assertIsInstance(tool_use["input"], dict)

    async def test_empty_text_block_is_dropped(self) -> None:
        """Empty ``TextBlock`` must be skipped, not forwarded.

        Anthropic rejects ``{"type": "text", "text": ""}`` with a 400
        ("text blocks must be non-empty"). Empty text blocks arise in
        practice after a tool-call-only assistant turn whose streamed text
        block is empty. The formatter must drop them rather than emit them.
        """
        fmt = AnthropicChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    TextBlock(text=""),
                    ToolCallBlock(
                        id="call_1",
                        name="get_weather",
                        input='{"city": "Tokyo"}',
                    ),
                ],
            ),
        ]

        res = await fmt.format(msgs)

        # The empty TextBlock is dropped — the assistant message contains
        # only the tool_use block, nothing else.
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "get_weather",
                            "input": {"city": "Tokyo"},
                        },
                    ],
                },
            ],
            res,
        )

    async def test_empty_tool_result_text_is_dropped(self) -> None:
        """Empty text inside a tool result is skipped too.

        A ``ToolResultBlock`` whose only output is an empty TextBlock must
        not produce an empty text content entry, which Anthropic would
        reject. The formatter falls back to a non-empty placeholder so the
        tool_result content list stays valid.
        """
        fmt = AnthropicChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call_1",
                        name="get_weather",
                        input='{"city": "Tokyo"}',
                    ),
                ],
            ),
            AssistantMsg(
                name="assistant",
                content=[
                    ToolResultBlock(
                        id="call_1",
                        name="get_weather",
                        output=[TextBlock(text="")],
                        state=ToolResultState.SUCCESS,
                    ),
                ],
            ),
        ]

        res = await fmt.format(msgs)

        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "get_weather",
                            "input": {"city": "Tokyo"},
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
                                    "text": "(empty tool output)",
                                },
                            ],
                        },
                    ],
                },
            ],
            res,
        )

    async def test_empty_string_tool_output_uses_placeholder(self) -> None:
        """An empty-string tool output becomes a placeholder text block.

        Anthropic rejects both empty text blocks and a tool_result whose
        content list is empty, so a wholly-empty output must be replaced
        with a non-empty placeholder.
        """
        fmt = AnthropicChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call_1",
                        name="noop",
                        input="{}",
                    ),
                ],
            ),
            AssistantMsg(
                name="assistant",
                content=[
                    ToolResultBlock(
                        id="call_1",
                        name="noop",
                        output="",
                        state=ToolResultState.SUCCESS,
                    ),
                ],
            ),
        ]

        res = await fmt.format(msgs)

        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "noop",
                            "input": {},
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
                                    "text": "(empty tool output)",
                                },
                            ],
                        },
                    ],
                },
            ],
            res,
        )
