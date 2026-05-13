# -*- coding: utf-8 -*-
"""Comprehensive formatter unit tests for OpenAIChatFormatter and
OpenAIMultiAgentFormatter, following the reference test style with exact
ground-truth comparisons.
"""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import shortuuid

from agentscope.formatter import (
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
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


class TestOpenAIFormatter(IsolatedAsyncioTestCase):
    """Comprehensive tests for OpenAI Chat and MultiAgent formatters."""

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
        # Message fixtures
        # (No audio in conversation: OpenAI URL audio requires a download)
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
                            url=self.image_url,
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
        # Ground truth: OpenAIChatFormatter
        #   - Content is a list of {"type": ..., ...} dicts.
        #   - Tool-result content is a plain string.
        #   - Messages have a "name" field.
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
                        "image_url": {"url": self.image_url},
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
                    {
                        "type": "text",
                        "text": "The capital of Japan is Tokyo.",
                    },
                ],
            },
        ]

        # ---------------------------------------------------------------
        # Ground truth: OpenAIMultiAgentFormatter
        #   - System content is a plain string.
        #   - All conversation text is collapsed into a single text block,
        #     with media blocks appended after.
        #   - No "name" field on the user wrapper message.
        # ---------------------------------------------------------------
        _hist_prompt = OpenAIMultiAgentFormatter().conversation_history_prompt

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
                        "image_url": {"url": self.image_url},
                    },
                ],
            },
            self._gt_tool_call,
            self._gt_tool_result,
            _gt_trailing_asst_nonfirst,
        ]

    # -------------------------------------------------------------------
    # OpenAIChatFormatter tests
    # -------------------------------------------------------------------

    async def test_chat_formatter(self) -> None:
        """Chat formatter produces exact output for various subsets."""
        fmt = OpenAIChatFormatter()
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
        """Base64-encoded image is inlined as a data URI."""
        fmt = OpenAIChatFormatter()
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

    async def test_chat_formatter_thinking_dropped(self) -> None:
        """ThinkingBlock is silently dropped by OpenAI formatter."""
        fmt = OpenAIChatFormatter()
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
        self.assertNotIn("reasoning_content", res[0])
        self.assertEqual(
            res[0]["content"],
            [{"type": "text", "text": "reply"}],
        )

    async def test_chat_formatter_url_image_in_tool_result(self) -> None:
        """URL images in tool results are promoted to a follow-up user
        message."""
        with patch.object(shortuuid, "uuid", return_value=_FIXED_ID):
            fmt = OpenAIChatFormatter()
            msgs = [
                Msg(
                    name="assistant",
                    content=[
                        ToolCallBlock(
                            id="call_img",
                            name="get_map",
                            input='{"city": "Tokyo"}',
                        ),
                    ],
                    role="assistant",
                ),
                Msg(
                    name="tool",
                    content=[
                        ToolResultBlock(
                            id="call_img",
                            name="get_map",
                            output=[
                                TextBlock(
                                    type="text",
                                    text="Here is the map.",
                                ),
                                DataBlock(
                                    source=URLSource(
                                        url=self.image_url,
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

        expected_tool_content = (
            "Here is the map.\n"
            f"<system-reminder>A(n) image file is returned "
            f"and will be presented to you with the identifier "
            f"[{_FIXED_ID}].</system-reminder>"
        )
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0]["role"], "assistant")
        self.assertEqual(res[1]["role"], "tool")
        self.assertEqual(res[1]["content"], expected_tool_content)
        # The promoted multimodal user message
        self.assertEqual(res[2]["role"], "user")
        image_blocks = [
            b
            for b in res[2]["content"]
            if b.get("type") == "image_url"
            and b["image_url"]["url"] == self.image_url
        ]
        self.assertEqual(len(image_blocks), 1)

    # -------------------------------------------------------------------
    # OpenAIMultiAgentFormatter tests
    # -------------------------------------------------------------------

    async def test_multiagent_formatter(self) -> None:
        """MultiAgent formatter produces exact output for various subsets."""
        fmt = OpenAIMultiAgentFormatter()
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

        # Tools only — is_first=True for the trailing assistant message
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
