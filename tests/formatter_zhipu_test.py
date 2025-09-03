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
                "你是一个有用的助手。",
                "system",
            ),
        ]

        self.msgs_conversation = [
            Msg(
                "user",
                [
                    TextBlock(
                        type="text",
                        text="法国的首都是什么？",
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
                "法国的首都是巴黎。",
                "assistant",
            ),
            Msg(
                "user",
                [
                    TextBlock(
                        type="text",
                        text="德国的首都是什么？",
                    ),
                ],
                "user",
            ),
            Msg(
                "assistant",
                "德国的首都是柏林。",
                "assistant",
            ),
            Msg(
                "user",
                "日本的首都是什么？",
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
                        input={"country": "日本"},
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
                                text="日本的首都是东京。",
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
                "日本的首都是东京。",
                "assistant",
            ),
        ]

        self.msgs_thinking = [
            Msg(
                "assistant",
                [
                    ThinkingBlock(
                        type="thinking",
                        thinking="让我思考一下这个问题...",
                    ),
                    TextBlock(
                        type="text",
                        text="经过思考，我认为答案是...",
                    ),
                ],
                "assistant",
            ),
        ]

        self.ground_truth_chat = [
            {
                "role": "system",
                "content": "你是一个有用的助手。",
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "法国的首都是什么？",
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
                "content": "法国的首都是巴黎。",
            },
            {
                "role": "user",
                "content": "德国的首都是什么？",
            },
            {
                "role": "assistant",
                "content": "德国的首都是柏林。",
            },
            {
                "role": "user",
                "content": "日本的首都是什么？",
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
                            "arguments": '{"country": "日本"}',
                        },
                    },
                ],
            },
            {
                "role": "user",
                "content": "- 日本的首都是东京。\n- The returned image can be found at: /Users/feizekai/sourcecode/agentscope/image.png",
            },
            {
                "role": "assistant",
                "content": "日本的首都是东京。",
            },
        ]

        self.ground_truth_thinking = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "[思考] 让我思考一下这个问题...",
                    },
                    {
                        "type": "text",
                        "text": "经过思考，我认为答案是...",
                    },
                ],
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
                        text="请分析这张图片",
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
                        "text": "请分析这张图片",
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
                "你好",
                "user",
            ),
            Msg(
                "Bob",
                "你好，Alice！",
                "assistant",
            ),
        ]

        expected = [
            {
                "role": "user",
                "content": "你好",
                "name": "Alice",
            },
            {
                "role": "assistant",
                "content": "你好，Alice！",
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
                "你好",
                "user",
            ),
            Msg(
                "assistant",  # name same as role
                "你好！",
                "assistant",
            ),
        ]

        expected = [
            {
                "role": "user",
                "content": "你好",
            },
            {
                "role": "assistant",
                "content": "你好！",
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
                    TextBlock(type="text", text="请看这张图片"),
                    ImageBlock(
                        type="image",
                        source=URLSource(type="url", url=self.image_path),
                    ),
                    ThinkingBlock(type="thinking", thinking="用户想要我分析图片"),
                ],
                "user",
            ),
        ]

        formatter = ZhipuChatFormatter()
        formatted = await formatter.format(msgs)

        # 验证格式化结果包含所有内容类型
        self.assertEqual(len(formatted), 1)
        self.assertEqual(formatted[0]["role"], "user")
        self.assertIsInstance(formatted[0]["content"], list)
        self.assertEqual(len(formatted[0]["content"]), 3)  # text + image + thinking

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

