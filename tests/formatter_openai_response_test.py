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

from agentscope.formatter import (
    OpenAIResponseFormatter,
    OpenAIResponseMultiAgentFormatter,
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
    URLSource,
    ThinkingBlock,
    HintBlock,
)


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
                        source=URLSource(
                            url=self.image_url,
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
                        "type": "output_text",
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
                        "type": "output_text",
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
                        "type": "output_text",
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

        self._gt_trailing_asst = {
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "The capital of Japan is Tokyo.",
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
            self._gt_trailing_asst,
        ]

    # -------------------------------------------------------------------
    # OpenAIResponseFormatter tests
    # -------------------------------------------------------------------

    async def test_chat_formatter(self) -> None:
        """Chat formatter produces exact output for various subsets."""
        fmt = OpenAIResponseFormatter()

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
        """Base64-encoded image becomes an input_image item with data URI."""
        fmt = OpenAIResponseFormatter()
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
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "reply"},
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_thinking_echoed_with_reasoning_item_id(
        self,
    ) -> None:
        """ThinkingBlock with reasoning_item_id is echoed as a reasoning
        item."""
        fmt = OpenAIResponseFormatter()
        thinking = ThinkingBlock(thinking="my reasoning")
        thinking.reasoning_item_id = "rs_001"
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[thinking, TextBlock(text="reply")],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "type": "reasoning",
                    "id": "rs_001",
                    "summary": [
                        {"type": "summary_text", "text": "my reasoning"},
                    ],
                    "content": [],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "reply"},
                    ],
                },
            ],
            res,
        )

    @patch(
        "agentscope.formatter._formatter_base.shortuuid.uuid",
        return_value=_FIXED_ID,
    )
    async def test_chat_formatter_url_image_in_tool_result(
        self,
        _mock_uuid: object,
    ) -> None:
        """URL images in tool results are promoted to a follow-up user
        message."""
        fmt = OpenAIResponseFormatter()
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
                                source=URLSource(
                                    url=self.image_url,
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

        expected_tool_content = (
            "Here is the map.\n"
            f"<system-reminder>A(n) image file is returned "
            f"and will be presented to you with the identifier "
            f"[{_FIXED_ID}].</system-reminder>"
        )
        self.assertListEqual(
            [
                {
                    "type": "function_call",
                    "id": "call_img",
                    "call_id": "call_img",
                    "name": "get_map",
                    "arguments": '{"city": "Tokyo"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_img",
                    "output": expected_tool_content,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "<system-reminder>The multimodal data "
                                "and their identifiers are listed as "
                                "follows:"
                            ),
                        },
                        {
                            "type": "input_text",
                            "text": f"- {_FIXED_ID} (image file): ",
                        },
                        {
                            "type": "input_image",
                            "image_url": self.image_url,
                        },
                        {
                            "type": "input_text",
                            "text": "</system-reminder>",
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Here is the map of Tokyo.",
                        },
                    ],
                },
            ],
            res,
        )

    # -------------------------------------------------------------------
    # OpenAIResponseMultiAgentFormatter tests
    # -------------------------------------------------------------------

    async def test_multiagent_formatter(self) -> None:
        """MultiAgent formatter produces exact output for various subsets."""
        fmt = OpenAIResponseMultiAgentFormatter()

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
        fmt = OpenAIResponseFormatter()
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
                    "content": [
                        {"type": "output_text", "text": "text_1"},
                    ],
                },
                {
                    "type": "function_call",
                    "id": "call_1",
                    "call_id": "call_1",
                    "name": "func_1",
                    "arguments": '{"arg": "value1"}',
                },
                {
                    "type": "function_call",
                    "id": "call_2",
                    "call_id": "call_2",
                    "name": "func_2",
                    "arguments": '{"arg": "value2"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "result_1",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_2",
                    "output": "result_2",
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "text_2"},
                    ],
                },
                {
                    "type": "function_call",
                    "id": "call_3",
                    "call_id": "call_3",
                    "name": "func_3",
                    "arguments": '{"arg": "value3"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_3",
                    "output": "result_3",
                },
                {
                    "type": "function_call",
                    "id": "call_4",
                    "call_id": "call_4",
                    "name": "func_4",
                    "arguments": '{"arg": "value4"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_4",
                    "output": "result_4",
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "text_3"},
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_hint_block(self) -> None:
        """HintBlock flushes preceding content and becomes a user message."""
        fmt = OpenAIResponseFormatter()
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
                        {
                            "type": "output_text",
                            "text": "Let me think about that.",
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Remember to be concise.",
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Here is my answer.",
                        },
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_hint_block_multimodal(self) -> None:
        """Multimodal HintBlock becomes a single user message with text +
        image."""
        fmt = OpenAIResponseFormatter()
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
                            "type": "input_text",
                            "text": "Inspect this screenshot:",
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

    async def test_tool_call_two_id_scenario(self) -> None:
        """When ToolCallBlock carries a separate call_id (OpenAI Responses
        API two-ID style), the formatter must use call_id (not id) for
        function_call_output."""
        fmt = OpenAIResponseFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(
                        id="fc_abc",
                        call_id="call-1",
                        name="search",
                        input='{"q": "test"}',
                    ),
                    ToolResultBlock(
                        id="fc_abc",
                        call_id="call-1",
                        name="search",
                        output=[TextBlock(text="result")],
                        state=ToolResultState.SUCCESS,
                    ),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "type": "function_call",
                    "id": "fc_abc",
                    "call_id": "call-1",
                    "name": "search",
                    "arguments": '{"q": "test"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "result",
                },
            ],
            res,
        )
