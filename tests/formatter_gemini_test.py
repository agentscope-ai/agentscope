# -*- coding: utf-8 -*-
"""Comprehensive formatter unit tests for GeminiChatFormatter and
GeminiMultiAgentFormatter, following the reference test style with exact
ground-truth comparisons.
"""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.formatter import (
    GeminiChatFormatter,
    GeminiMultiAgentFormatter,
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


_FIXED_ID = "TESTID1234567"


class TestGeminiFormatter(IsolatedAsyncioTestCase):
    """Comprehensive tests for Gemini Chat and MultiAgent formatters."""

    async def asyncSetUp(self) -> None:
        """Set up shared message fixtures and expected ground-truth dicts."""
        self.image_b64 = "ZmFrZSBpbWFnZSBkYXRh"

        # ---------------------------------------------------------------
        # Message fixtures
        # (Use base64 images: Gemini URL handling downloads from the network)
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
                content=[
                    TextBlock(text="What is the capital of France?"),
                    DataBlock(
                        source=Base64Source(
                            data=self.image_b64,
                            media_type="image/png",
                        ),
                    ),
                ],
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

        _inline_img = {
            "inline_data": {"data": self.image_b64, "mime_type": "image/png"},
        }

        # ---------------------------------------------------------------
        # Ground truth: GeminiChatFormatter
        #   - System message becomes role="user" (no special system role).
        #   - Assistant messages become role="model".
        #   - Content is in "parts" (not "content") as a list of dicts.
        #   - ToolCallBlock becomes "function_call" part.
        #   - ToolResultBlock becomes a separate role="user" message with
        #     "function_response" part.
        # ---------------------------------------------------------------
        self.gt_chat = [
            {
                "role": "user",
                "parts": [{"text": "You're a helpful assistant."}],
            },
            {
                "role": "user",
                "parts": [
                    {"text": "What is the capital of France?"},
                    _inline_img,
                ],
            },
            {
                "role": "model",
                "parts": [{"text": "The capital of France is Paris."}],
            },
            {
                "role": "user",
                "parts": [{"text": "What is the capital of Germany?"}],
            },
            {
                "role": "model",
                "parts": [{"text": "The capital of Germany is Berlin."}],
            },
            {
                "role": "user",
                "parts": [{"text": "What is the capital of Japan?"}],
            },
            {
                "role": "model",
                "parts": [
                    {
                        "function_call": {
                            "id": "call_1",
                            "name": "get_capital",
                            "args": {"country": "Japan"},
                        },
                    },
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "function_response": {
                            "id": "call_1",
                            "name": "get_capital",
                            "response": {
                                "output": "The capital of Japan is Tokyo.",
                            },
                        },
                    },
                ],
            },
            {
                "role": "model",
                "parts": [{"text": "The capital of Japan is Tokyo."}],
            },
        ]

        # ---------------------------------------------------------------
        # Ground truth: GeminiMultiAgentFormatter
        #   - System message: role="user" (same as chat formatter).
        #   - Agent messages: collapsed into role="user" with parts list.
        #   - Media blocks interleaved (text flushed before each DataBlock).
        #   - is_first=False still wraps with <history> (no hist_prompt
        #     prefix).
        # ---------------------------------------------------------------
        _hist_prompt = GeminiMultiAgentFormatter().conversation_history_prompt

        self._gt_trailing_asst = {
            "role": "model",
            "parts": [
                {"text": "The capital of Japan is Tokyo."},
            ],
        }

        self._gt_tool_call = {
            "role": "model",
            "parts": [
                {
                    "function_call": {
                        "id": "call_1",
                        "name": "get_capital",
                        "args": {"country": "Japan"},
                    },
                },
            ],
        }
        self._gt_tool_result = {
            "role": "user",
            "parts": [
                {
                    "function_response": {
                        "id": "call_1",
                        "name": "get_capital",
                        "response": {
                            "output": "The capital of Japan is Tokyo.",
                        },
                    },
                },
            ],
        }

        self.gt_multiagent = [
            {
                "role": "user",
                "parts": [{"text": "You're a helpful assistant."}],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            _hist_prompt + "<history>\n"
                            "user: What is the capital of France?"
                        ),
                    },
                    _inline_img,
                    {
                        "text": (
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

    # -------------------------------------------------------------------
    # GeminiChatFormatter tests
    # -------------------------------------------------------------------

    async def test_chat_formatter(self) -> None:
        """Chat formatter produces exact output for various subsets."""
        fmt = GeminiChatFormatter()

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

    async def test_chat_formatter_thinking_preserved(self) -> None:
        """ThinkingBlock becomes a part with thought=True in Gemini format."""
        fmt = GeminiChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="inner thoughts"),
                    TextBlock(text="reply"),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "model",
                    "parts": [
                        {"thought": True, "text": "inner thoughts"},
                        {"text": "reply"},
                    ],
                },
            ],
            res,
        )

    async def test_empty_thinking_block_is_dropped(self) -> None:
        """An empty ``ThinkingBlock`` must be skipped, not forwarded.

        Gemini rejects a thought part whose text is empty with a 400
        ("contents.parts must not be empty"). Empty thinking blocks occur
        when a reasoning model returns no summary, so the formatter must
        drop them rather than emit ``{"thought": True, "text": ""}``.
        """
        fmt = GeminiChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking=""),
                    TextBlock(text="reply"),
                ],
            ),
        ]

        res = await fmt.format(msgs)

        # Only the text part remains — no empty thought part.
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["role"], "model")
        thought_parts = [
            p for p in res[0]["parts"] if p.get("thought") is True
        ]
        self.assertEqual(thought_parts, [])
        self.assertEqual(res[0]["parts"], [{"text": "reply"}])

    @patch(
        "agentscope.formatter._formatter_base.shortuuid.uuid",
        return_value=_FIXED_ID,
    )
    async def test_chat_formatter_base64_image_in_tool_result(
        self,
        _mock_uuid: object,
    ) -> None:
        """Base64 images in tool results are promoted to a follow-up user
        message."""
        fmt = GeminiChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="call_img",
                        name="get_map",
                        input='{"city": "Tokyo"}',
                    ),
                    ToolResultBlock(
                        id="call_img",
                        name="get_map",
                        output=[
                            TextBlock(text="Here is the map."),
                            DataBlock(
                                source=Base64Source(
                                    data=self.image_b64,
                                    media_type="image/png",
                                ),
                            ),
                        ],
                        state=ToolResultState.SUCCESS,
                    ),
                    TextBlock(text="Here is the map of Tokyo."),
                ],
            ),
        ]
        res = await fmt.format(msgs)

        expected_tool_output = (
            "Here is the map.\n"
            f"<system-reminder>A(n) image file is returned "
            f"and will be presented to you with the identifier "
            f"[{_FIXED_ID}].</system-reminder>"
        )
        self.assertListEqual(
            [
                {
                    "role": "model",
                    "parts": [
                        {
                            "function_call": {
                                "id": "call_img",
                                "name": "get_map",
                                "args": {"city": "Tokyo"},
                            },
                        },
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "id": "call_img",
                                "name": "get_map",
                                "response": {"output": expected_tool_output},
                            },
                        },
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "<system-reminder>The multimodal data "
                                "and their identifiers are listed as "
                                "follows:"
                            ),
                        },
                        {
                            "text": f"- {_FIXED_ID} (image file): ",
                        },
                        {
                            "inline_data": {
                                "data": self.image_b64,
                                "mime_type": "image/png",
                            },
                        },
                        {"text": "</system-reminder>"},
                    ],
                },
                {
                    "role": "model",
                    "parts": [
                        {"text": "Here is the map of Tokyo."},
                    ],
                },
            ],
            res,
        )

    # -------------------------------------------------------------------
    # GeminiMultiAgentFormatter tests
    # -------------------------------------------------------------------

    async def test_multiagent_formatter(self) -> None:
        """MultiAgent formatter produces exact output for various subsets."""
        fmt = GeminiMultiAgentFormatter()

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
        res = await fmt.format([])
        self.assertListEqual([], res)

    async def test_chat_formatter_complex_multi_step(self) -> None:
        """Complex multi-step sequence with interleaved thinking, text,
        tool calls, and tool results."""
        fmt = GeminiChatFormatter()
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
                    "role": "model",
                    "parts": [
                        {"thought": True, "text": "thinking_1"},
                        {"text": "text_1"},
                        {
                            "function_call": {
                                "id": "call_1",
                                "name": "func_1",
                                "args": {"arg": "value1"},
                            },
                        },
                        {
                            "function_call": {
                                "id": "call_2",
                                "name": "func_2",
                                "args": {"arg": "value2"},
                            },
                        },
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "id": "call_1",
                                "name": "func_1",
                                "response": {"output": "result_1"},
                            },
                        },
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "id": "call_2",
                                "name": "func_2",
                                "response": {"output": "result_2"},
                            },
                        },
                    ],
                },
                {
                    "role": "model",
                    "parts": [
                        {"thought": True, "text": "thinking_2"},
                        {"text": "text_2"},
                        {
                            "function_call": {
                                "id": "call_3",
                                "name": "func_3",
                                "args": {"arg": "value3"},
                            },
                        },
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "id": "call_3",
                                "name": "func_3",
                                "response": {"output": "result_3"},
                            },
                        },
                    ],
                },
                {
                    "role": "model",
                    "parts": [
                        {
                            "function_call": {
                                "id": "call_4",
                                "name": "func_4",
                                "args": {"arg": "value4"},
                            },
                        },
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "id": "call_4",
                                "name": "func_4",
                                "response": {"output": "result_4"},
                            },
                        },
                    ],
                },
                {
                    "role": "model",
                    "parts": [
                        {"thought": True, "text": "thinking_3"},
                        {"text": "text_3"},
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_hint_block(self) -> None:
        """HintBlock flushes preceding content and becomes a user message."""
        fmt = GeminiChatFormatter()
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
                    "role": "model",
                    "parts": [
                        {"text": "Let me think about that."},
                    ],
                },
                {
                    "role": "user",
                    "parts": [
                        {"text": "Remember to be concise."},
                    ],
                },
                {
                    "role": "model",
                    "parts": [
                        {"text": "Here is my answer."},
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_hint_block_multimodal(self) -> None:
        """Multimodal HintBlock becomes a single user message with text +
        image."""
        fmt = GeminiChatFormatter()
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
                    "parts": [
                        {"text": "Inspect this screenshot:"},
                        {
                            "inline_data": {
                                "data": self.image_b64,
                                "mime_type": "image/png",
                            },
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
        ``ToolCallBlock.input`` as an incomplete JSON fragment. The
        formatter must repair it into a dict instead of raising
        ``JSONDecodeError``.
        """
        fmt = GeminiChatFormatter()
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

        function_call = [
            part
            for m in res
            for part in m.get("parts", [])
            if "function_call" in part
        ][0]["function_call"]
        self.assertIsInstance(function_call["args"], dict)
