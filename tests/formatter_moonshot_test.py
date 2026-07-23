# -*- coding: utf-8 -*-
"""Comprehensive formatter unit tests for MoonshotChatFormatter and
MoonshotMultiAgentFormatter, following the reference test style with exact
ground-truth comparisons.
"""
import base64
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch, MagicMock

from agentscope.formatter import (
    MoonshotChatFormatter,
    MoonshotMultiAgentFormatter,
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


class TestMoonshotFormatter(IsolatedAsyncioTestCase):
    """Comprehensive tests for Moonshot Chat and MultiAgent formatters."""

    async def asyncSetUp(self) -> None:
        """Set up shared message fixtures and expected ground-truth dicts."""
        _img_src = URLSource(
            url="https://example.com/image.png",
            media_type="image/png",
        )
        self.image_url = str(_img_src.url)

        # The Moonshot formatter downloads remote image URLs and inlines
        # them as base64 data URIs (the Moonshot vision API rejects raw
        # HTTPS URLs). Patch `requests.get` so tests don't hit the network
        # and produce a deterministic payload that matches `image_b64`.
        self.image_b64 = "ZmFrZSBpbWFnZSBkYXRh"
        self.image_bytes = base64.b64decode(self.image_b64)
        self.image_data_uri = f"data:image/png;base64,{self.image_b64}"

        mock_response = MagicMock()
        mock_response.content = self.image_bytes
        mock_response.raise_for_status = MagicMock()
        self._requests_get_patcher = patch(
            "agentscope.formatter._moonshot_formatter.requests.get",
            return_value=mock_response,
        )
        self._requests_get_patcher.start()
        self.addCleanup(self._requests_get_patcher.stop)

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
        # Ground truth: MoonshotChatFormatter
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
                        "image_url": {"url": self.image_data_uri},
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
        # Ground truth: MoonshotMultiAgentFormatter
        #   - Same as OpenAI MultiAgent, but tool-sequence assistant messages
        #     carry "reasoning_content": "".
        # ---------------------------------------------------------------
        _hist_prompt = (
            MoonshotMultiAgentFormatter().conversation_history_prompt
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
            "name": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "The capital of Japan is Tokyo.",
                },
            ],
            "reasoning_content": "",
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
                        "image_url": {"url": self.image_data_uri},
                    },
                ],
            },
            self._gt_tool_call,
            self._gt_tool_result,
            self._gt_trailing_asst,
        ]

    # -------------------------------------------------------------------
    # MoonshotChatFormatter tests
    # -------------------------------------------------------------------

    async def test_chat_formatter(self) -> None:
        """Chat formatter produces exact output for various subsets."""
        fmt = MoonshotChatFormatter()

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

    async def test_chat_formatter_image_extra_params(self) -> None:
        """DataBlock extra params are nested inside image_url payload."""
        fmt = MoonshotChatFormatter()
        msgs = [
            UserMsg(
                name="user",
                content=[
                    TextBlock(text="Inspect this image."),
                    DataBlock(
                        source=URLSource(
                            url=self.image_url,
                            media_type="image/png",
                        ),
                        detail="high",
                    ),
                ],
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "user",
                    "name": "user",
                    "content": [
                        {"type": "text", "text": "Inspect this image."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": self.image_data_uri,
                                "detail": "high",
                            },
                        },
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_thinking_to_reasoning_content(
        self,
    ) -> None:
        """ThinkingBlock becomes reasoning_content in Moonshot (Preserved
        Thinking)."""
        fmt = MoonshotChatFormatter()
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
                    "name": "assistant",
                    "content": [{"type": "text", "text": "reply"}],
                    "reasoning_content": "inner thoughts",
                },
            ],
            res,
        )

    async def test_chat_formatter_assistant_always_has_reasoning_content(
        self,
    ) -> None:
        """All assistant messages always have reasoning_content (even when
        empty)."""
        fmt = MoonshotChatFormatter()
        msgs = [
            AssistantMsg(
                name="assistant",
                content="Hello!",
            ),
        ]
        res = await fmt.format(msgs)
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "name": "assistant",
                    "content": [{"type": "text", "text": "Hello!"}],
                    "reasoning_content": "",
                },
            ],
            res,
        )

    async def test_chat_formatter_base64_image(self) -> None:
        """Base64-encoded image is inlined as a data URI."""
        fmt = MoonshotChatFormatter()
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
        fmt = MoonshotChatFormatter()
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
                    "role": "assistant",
                    "name": "assistant",
                    "content": None,
                    "reasoning_content": "",
                    "tool_calls": [
                        {
                            "id": "call_img",
                            "type": "function",
                            "function": {
                                "name": "get_map",
                                "arguments": '{"city": "Tokyo"}',
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_img",
                    "content": expected_tool_content,
                    "name": "get_map",
                },
                {
                    "role": "user",
                    "name": "system-reminder",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "<system-reminder>The multimodal data "
                                "and their identifiers are listed as "
                                "follows:"
                            ),
                        },
                        {
                            "type": "text",
                            "text": f"- {_FIXED_ID} (image file): ",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": self.image_data_uri},
                        },
                        {
                            "type": "text",
                            "text": "</system-reminder>",
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "name": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "Here is the map of Tokyo.",
                        },
                    ],
                    "reasoning_content": "",
                },
            ],
            res,
        )

    # -------------------------------------------------------------------
    # MoonshotMultiAgentFormatter tests
    # -------------------------------------------------------------------

    async def test_multiagent_formatter(self) -> None:
        """MultiAgent formatter produces exact output for various subsets."""
        fmt = MoonshotMultiAgentFormatter()

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
        fmt = MoonshotChatFormatter()
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
                    "name": "assistant",
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
                    "name": "assistant",
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
                    "name": "assistant",
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
                    "name": "assistant",
                    "content": [{"type": "text", "text": "text_3"}],
                    "reasoning_content": "thinking_3",
                },
            ],
            res,
        )

    async def test_chat_formatter_hint_block(self) -> None:
        """HintBlock flushes preceding content and becomes a user message."""
        fmt = MoonshotChatFormatter()
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
                    "name": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me think about that."},
                    ],
                    "reasoning_content": "",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Remember to be concise."},
                    ],
                },
                {
                    "role": "assistant",
                    "name": "assistant",
                    "content": [
                        {"type": "text", "text": "Here is my answer."},
                    ],
                    "reasoning_content": "",
                },
            ],
            res,
        )

    async def test_chat_formatter_hint_block_multimodal(self) -> None:
        """Multimodal HintBlock becomes a single user message with text +
        image."""
        fmt = MoonshotChatFormatter()
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
                            "type": "image_url",
                            "image_url": {"url": self.image_data_uri},
                        },
                    ],
                },
            ],
            res,
        )
