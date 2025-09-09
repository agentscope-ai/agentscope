# -*- coding: utf-8 -*-
"""The Zhipu AI formatter unittests."""
import os
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.formatter import ZhipuChatFormatter, ZhipuMultiAgentFormatter
from agentscope.message import (
    Msg,
    TextBlock,
    ImageBlock,
    URLSource,
    ToolResultBlock,
    ToolUseBlock,
    Base64Source,
    ThinkingBlock,
)


class TestZhipuFormatter(IsolatedAsyncioTestCase):
    """Zhipu AI formatter unittests."""

    async def asyncSetUp(self) -> None:
        """Set up the test environment."""
        self.image_path = os.path.abspath("./image.png")
        with open(self.image_path, "wb") as f:
            f.write(b"fake image content")

        self.msgs_system = [
            Msg(
                "system",
                "You are a helpful assistant.",
                "system",
            ),
        ]

        self.msgs_conversation = [
            Msg(
                "user",
                [
                    TextBlock(
                        type="text",
                        text="What is the capital of France?",
                    ),
                    ImageBlock(
                        type="image",
                        source=URLSource(
                            type="url",
                            url=self.image_path,
                        ),
                    ),
                ],
                "user",
            ),
            Msg(
                "assistant",
                "The capital of France is Paris.",
                "assistant",
            ),
            Msg(
                "user",
                [
                    TextBlock(
                        type="text",
                        text="What is the capital of Germany?",
                    ),
                ],
                "user",
            ),
            Msg(
                "assistant",
                "The capital of Germany is Berlin.",
                "assistant",
            ),
            Msg(
                "user",
                "What is the capital of Japan?",
                "user",
            ),
        ]

        self.msgs_tools = [
            Msg(
                "assistant",
                [
                    ToolUseBlock(
                        type="tool_use",
                        id="1",
                        name="get_capital",
                        input={"country": "Japan"},
                    ),
                ],
                "assistant",
            ),
            Msg(
                "user",
                [
                    ToolResultBlock(
                        type="tool_result",
                        id="1",
                        name="get_capital",
                        output=[
                            TextBlock(
                                type="text",
                                text="The capital of Japan is Tokyo.",
                            ),
                            ImageBlock(
                                type="image",
                                source=URLSource(
                                    type="url",
                                    url=self.image_path,
                                ),
                            ),
                        ],
                    ),
                ],
                "user",
            ),
            Msg(
                "assistant",
                "The capital of Japan is Tokyo.",
                "assistant",
            ),
        ]

        self.msgs_thinking = [
            Msg(
                "assistant",
                [
                    ThinkingBlock(
                        type="thinking",
                        thinking="Let me think about this question...",
                    ),
                    TextBlock(
                        type="text",
                        text="After thinking, I believe the answer is...",
                    ),
                ],
                "assistant",
            ),
        ]

        self.ground_truth_chat = [
            {
                "role": "system",
                "content": "You are a helpful assistant.",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the capital of France?",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,ZmFrZSBpbWFnZSBjb250ZW50",
                        },
                    },
                ],
            },
            {
                "role": "assistant",
                "content": "The capital of France is Paris.",
            },
            {
                "role": "user",
                "content": "What is the capital of Germany?",
            },
            {
                "role": "assistant",
                "content": "The capital of Germany is Berlin.",
            },
            {
                "role": "user",
                "content": "What is the capital of Japan?",
            },
        ]

        self.ground_truth_tools = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "1",
                        "type": "function",
                        "function": {
                            "name": "get_capital",
                            "arguments": '{"country": "Japan"}',
                        },
                    },
                ],
            },
            {
                "role": "user",
                "content": "- The capital of Japan is Tokyo.\n- The returned image can be found at: "
                + self.image_path,
            },
            {
                "role": "assistant",
                "content": "The capital of Japan is Tokyo.",
            },
        ]

        self.ground_truth_thinking = [
            {
                "role": "assistant",
                "content": "After thinking, I believe the answer is...",
            },
        ]

    async def asyncTearDown(self) -> None:
        """Clean up the test environment."""
        if os.path.exists(self.image_path):
            os.remove(self.image_path)

    async def test_zhipu_chat_formatter_basic(self) -> None:
        """Test basic ZhipuChatFormatter functionality."""
        formatter = ZhipuChatFormatter()
        formatted = await formatter.format(
            self.msgs_system + self.msgs_conversation,
        )
        self.assertEqual(formatted, self.ground_truth_chat)

    async def test_zhipu_chat_formatter_tools(self) -> None:
        """Test ZhipuChatFormatter with tool calls."""
        formatter = ZhipuChatFormatter()
        formatted = await formatter.format(self.msgs_tools)
        self.assertEqual(formatted, self.ground_truth_tools)

    async def test_zhipu_chat_formatter_thinking(self) -> None:
        """Test ZhipuChatFormatter with thinking blocks."""
        formatter = ZhipuChatFormatter()
        formatted = await formatter.format(self.msgs_thinking)
        self.assertEqual(formatted, self.ground_truth_thinking)

    async def test_zhipu_chat_formatter_empty_messages(self) -> None:
        """Test ZhipuChatFormatter with empty messages."""
        formatter = ZhipuChatFormatter()
        formatted = await formatter.format([])
        self.assertEqual(formatted, [])

    async def test_zhipu_chat_formatter_base64_image(self) -> None:
        """Test ZhipuChatFormatter with base64 encoded images."""
        msgs = [
            Msg(
                "user",
                [
                    TextBlock(
                        type="text",
                        text="Please analyze this image",
                    ),
                    ImageBlock(
                        type="image",
                        source=Base64Source(
                            type="base64",
                            media_type="image/png",
                            data="ZmFrZSBpbWFnZSBjb250ZW50",
                        ),
                    ),
                ],
                "user",
            ),
        ]

        expected = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Please analyze this image",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,ZmFrZSBpbWFnZSBjb250ZW50",
                        },
                    },
                ],
            },
        ]

        formatter = ZhipuChatFormatter()
        formatted = await formatter.format(msgs)
        self.assertEqual(formatted, expected)

    async def test_zhipu_multiagent_formatter(self) -> None:
        """Test ZhipuMultiAgentFormatter functionality."""
        msgs = [
            Msg(
                "Alice",
                "Hello",
                "user",
            ),
            Msg(
                "Bob",
                "Hello, Alice!",
                "assistant",
            ),
        ]

        expected = [
            {
                "role": "user",
                "content": "Hello",
                "name": "Alice",
            },
            {
                "role": "assistant",
                "content": "Hello, Alice!",
                "name": "Bob",
            },
        ]

        formatter = ZhipuMultiAgentFormatter()
        formatted = await formatter.format(msgs)
        self.assertEqual(formatted, expected)

    async def test_zhipu_multiagent_formatter_no_name(self) -> None:
        """Test ZhipuMultiAgentFormatter when name equals role."""
        msgs = [
            Msg(
                "user",  # name same as role
                "Hello",
                "user",
            ),
            Msg(
                "assistant",  # name same as role
                "Hi!",
                "assistant",
            ),
        ]

        expected = [
            {
                "role": "user",
                "content": "Hello",
            },
            {
                "role": "assistant",
                "content": "Hi!",
            },
        ]

        formatter = ZhipuMultiAgentFormatter()
        formatted = await formatter.format(msgs)
        self.assertEqual(formatted, expected)

    async def test_zhipu_formatter_mixed_content(self) -> None:
        """Test ZhipuChatFormatter with mixed content types."""
        msgs = [
            Msg(
                "user",
                [
                    TextBlock(type="text", text="Please look at this image"),
                    ImageBlock(
                        type="image",
                        source=URLSource(type="url", url=self.image_path),
                    ),
                    ThinkingBlock(
                        type="thinking",
                        thinking="User wants me to analyze the image",
                    ),
                ],
                "user",
            ),
        ]

        formatter = ZhipuChatFormatter()
        formatted = await formatter.format(msgs)

        # Verify the formatted result contains all content types
        self.assertEqual(len(formatted), 1)
        self.assertEqual(formatted[0]["role"], "user")
        self.assertIsInstance(formatted[0]["content"], list)
        self.assertEqual(
            len(formatted[0]["content"]),
            2,
        )  # text + image (thinking is skipped)

    async def test_zhipu_formatter_tool_only_message(self) -> None:
        """Test ZhipuChatFormatter with tool-only messages."""
        msgs = [
            Msg(
                "assistant",
                [
                    ToolUseBlock(
                        type="tool_use",
                        id="test_id",
                        name="test_function",
                        input={"param": "value"},
                    ),
                ],
                "assistant",
            ),
        ]

        expected = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "test_id",
                        "type": "function",
                        "function": {
                            "name": "test_function",
                            "arguments": '{"param": "value"}',
                        },
                    },
                ],
            },
        ]

        formatter = ZhipuChatFormatter()
        formatted = await formatter.format(msgs)
        self.assertEqual(formatted, expected)

    async def test_zhipu_formatter_supported_blocks(self) -> None:
        """Test that ZhipuChatFormatter supports the expected block types."""
        formatter = ZhipuChatFormatter()
        expected_blocks = [
            TextBlock,
            ImageBlock,
            ToolUseBlock,
            ToolResultBlock,
            ThinkingBlock,
        ]
        self.assertEqual(formatter.supported_blocks, expected_blocks)

    async def test_zhipu_formatter_capabilities(self) -> None:
        """Test ZhipuChatFormatter capabilities."""
        formatter = ZhipuChatFormatter()
        self.assertTrue(formatter.support_tools_api)
        self.assertTrue(formatter.support_multiagent)
        self.assertTrue(formatter.support_vision)
