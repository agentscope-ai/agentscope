# -*- coding: utf-8 -*-
"""Comprehensive formatter unit tests for OllamaChatFormatter and
OllamaMultiAgentFormatter, with exact ground-truth comparisons.
"""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.formatter import OllamaChatFormatter, OllamaMultiAgentFormatter
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


class TestOllamaFormatter(IsolatedAsyncioTestCase):
    """Comprehensive tests for Ollama Chat and MultiAgent formatters."""

    async def asyncSetUp(self) -> None:
        """Set up shared fixtures and ground-truth dicts."""
        _hist_prompt = OllamaMultiAgentFormatter().conversation_history_prompt

        # Base64 image fixture
        self.image_b64 = "ZmFrZSBpbWFnZSBkYXRh"

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
        # Ollama content is always a plain string.
        # Tool calls use dict arguments (not JSON string).
        self.gt_chat = [
            {"role": "system", "content": "You're a helpful assistant."},
            {"role": "user", "content": "What is the capital of France?"},
            {
                "role": "assistant",
                "content": "The capital of France is Paris.",
            },
            {"role": "user", "content": "What is the capital of Germany?"},
            {
                "role": "assistant",
                "content": "The capital of Germany is Berlin.",
            },
            {"role": "user", "content": "What is the capital of Japan?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "get_capital",
                            "arguments": {"country": "Japan"},
                        },
                    },
                ],
            },
            {"role": "tool", "content": "The capital of Japan is Tokyo."},
            {"role": "assistant", "content": "The capital of Japan is Tokyo."},
        ]

        # --- MultiAgent formatter ground truth ---
        # System content is a plain string.
        # History is a plain string with format "name:\ntext" per message.
        # For is_first=False, there are NO <history> tags (only in
        # is_first=True).
        _conv_text = (
            "user:\nWhat is the capital of France?\n"
            "assistant:\nThe capital of France is Paris.\n"
            "user:\nWhat is the capital of Germany?\n"
            "assistant:\nThe capital of Germany is Berlin.\n"
            "user:\nWhat is the capital of Japan?"
        )
        self._gt_trailing_asst = {
            "role": "assistant",
            "content": "The capital of Japan is Tokyo.",
        }
        self._gt_tool_call = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "get_capital",
                        "arguments": {"country": "Japan"},
                    },
                },
            ],
        }
        self._gt_tool_result = {
            "role": "tool",
            "content": "The capital of Japan is Tokyo.",
        }

        self.gt_multiagent = [
            {"role": "system", "content": "You're a helpful assistant."},
            {
                "role": "user",
                "content": (
                    _hist_prompt + "<history>\n" + _conv_text + "\n</history>"
                ),
            },
            self._gt_tool_call,
            self._gt_tool_result,
            self._gt_trailing_asst,
        ]

    # ------------------------------------------------------------------
    # OllamaChatFormatter tests
    # ------------------------------------------------------------------

    async def test_chat_formatter(self) -> None:
        """Chat formatter produces exact output for various subsets."""
        fmt = OllamaChatFormatter()

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

    async def test_chat_formatter_tool_call_arguments_are_dict(self) -> None:
        """Ollama requires tool call arguments as a dict, not a JSON string."""
        fmt = OllamaChatFormatter()
        tc = ToolCallBlock(id="c1", name="search", input='{"q": "weather"}')
        res = await fmt.format(
            [AssistantMsg(name="assistant", content=[tc])],
        )
        self.assertListEqual(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "search",
                                "arguments": {"q": "weather"},
                            },
                        },
                    ],
                },
            ],
            res,
        )

    async def test_chat_formatter_base64_image(self) -> None:
        """Base64 image is placed in the 'images' list as a raw base64
        string."""
        fmt = OllamaChatFormatter()
        msgs = [
            UserMsg(
                name="user",
                content=[
                    TextBlock(text="What is this?"),
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
                    "content": "What is this?",
                    "images": [self.image_b64],
                },
            ],
            res,
        )

    @patch(
        "agentscope.formatter._formatter_base.shortuuid.uuid",
        return_value=_FIXED_ID,
    )
    async def test_chat_formatter_base64_image_in_tool_result(
        self,
        _mock_uuid: object,
    ) -> None:
        """Base64 images in tool results are promoted to a follow-up user
        message with images list."""
        fmt = OllamaChatFormatter()
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
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "get_map",
                                "arguments": {"city": "Tokyo"},
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "content": expected_tool_content,
                },
                {
                    "role": "user",
                    "content": (
                        "<system-reminder>The multimodal data "
                        "and their identifiers are listed as follows:\n"
                        f"- {_FIXED_ID} (image file): \n"
                        "</system-reminder>"
                    ),
                    "images": [self.image_b64],
                },
                {
                    "role": "assistant",
                    "content": "Here is the map of Tokyo.",
                },
            ],
            res,
        )

    # ------------------------------------------------------------------
    # OllamaMultiAgentFormatter tests
    # ------------------------------------------------------------------

    async def test_multiagent_formatter(self) -> None:
        """MultiAgent formatter produces exact output for various subsets."""
        fmt = OllamaMultiAgentFormatter()

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
        fmt = OllamaChatFormatter()
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
                    "tool_calls": [
                        {
                            "function": {
                                "name": "func_1",
                                "arguments": {"arg": "value1"},
                            },
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "func_2",
                                "arguments": {"arg": "value2"},
                            },
                        },
                    ],
                },
                {"role": "tool", "content": "result_1"},
                {"role": "tool", "content": "result_2"},
                {
                    "role": "assistant",
                    "content": "text_2",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "func_3",
                                "arguments": {"arg": "value3"},
                            },
                        },
                    ],
                },
                {"role": "tool", "content": "result_3"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "func_4",
                                "arguments": {"arg": "value4"},
                            },
                        },
                    ],
                },
                {"role": "tool", "content": "result_4"},
                {"role": "assistant", "content": "text_3"},
            ],
            res,
        )

    async def test_chat_formatter_hint_block(self) -> None:
        """HintBlock flushes preceding content and becomes a user message."""
        fmt = OllamaChatFormatter()
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
                },
                {
                    "role": "user",
                    "content": "Remember to be concise.",
                },
                {
                    "role": "assistant",
                    "content": "Here is my answer.",
                },
            ],
            res,
        )

    async def test_chat_formatter_hint_block_multimodal(self) -> None:
        """Multimodal HintBlock becomes one user message with text + images
        list."""
        fmt = OllamaChatFormatter()
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
                    "content": "Inspect this screenshot:",
                    "images": [self.image_b64],
                },
            ],
            res,
        )

    async def test_tool_call_with_incomplete_input_does_not_crash(
        self,
    ) -> None:
        """A truncated ``block.input`` must not crash the formatter.

        Context compression or interrupted streaming can leave a
        ``ToolCallBlock.input`` as an incomplete JSON fragment. Ollama
        expects ``arguments`` as a dict, so the formatter must repair the
        fragment instead of raising ``JSONDecodeError``.
        """
        fmt = OllamaChatFormatter()
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

        args = res[0]["tool_calls"][0]["function"]["arguments"]
        self.assertIsInstance(args, dict)
