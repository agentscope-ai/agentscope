# -*- coding: utf-8 -*-
"""Unit tests for Zhipu AI token counter."""
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.token import ZhipuTokenCounter


class ZhipuTokenCounterTest(IsolatedAsyncioTestCase):
    """The unittests for the Zhipu AI token counter."""

    async def asyncSetUp(self) -> None:
        """Set up the test case."""
        self.messages = [
            {
                "role": "system",
                "content": "你是一个有用的助手。",
            },
            {
                "role": "user",
                "content": "法国的首都是什么？",
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
            {
                "role": "assistant",
                "content": None,
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
                "role": "tool",
                "tool_call_id": "1",
                "content": "日本的首都是东京。",
            },
            {
                "role": "assistant",
                "content": "日本的首都是东京。",
            },
        ]

        self.multimodal_messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "这张图片里有什么？",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                        },
                    },
                ],
            },
            {
                "role": "assistant",
                "content": "这是一个1x1像素的透明图片。",
            },
        ]

        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_location",
                    "description": "获取用户的位置信息",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {
                                "type": "string",
                                "description": "要获取位置信息的城市",
                            },
                        },
                        "required": ["city"],
                    },
                },
            },
        ]

    async def test_zhipu_token_counter_basic(self) -> None:
        """Test the basic Zhipu AI token counter."""
        counter = ZhipuTokenCounter(
            model_name="glm-4",
        )
        n_tokens = await counter.count(self.messages)
        self.assertIsInstance(n_tokens, int)
        self.assertGreater(n_tokens, 0)

    async def test_zhipu_token_counter_with_tools(self) -> None:
        """Test the Zhipu AI token counter with tools."""
        counter = ZhipuTokenCounter(
            model_name="glm-4",
        )
        n_tokens = await counter.count(self.messages, self.tools)
        self.assertIsInstance(n_tokens, int)
        self.assertGreater(n_tokens, 0)

        # 有工具的token数应该比没有工具的多
        n_tokens_without_tools = await counter.count(self.messages)
        self.assertGreater(n_tokens, n_tokens_without_tools)

    async def test_zhipu_token_counter_vision_model(self) -> None:
        """Test the Zhipu AI token counter for vision models."""
        counter = ZhipuTokenCounter(
            model_name="glm-4v",
        )
        n_tokens = await counter.count(self.multimodal_messages)
        self.assertIsInstance(n_tokens, int)
        self.assertGreater(n_tokens, 0)

    async def test_zhipu_token_counter_empty_messages(self) -> None:
        """Test the Zhipu AI token counter with empty messages."""
        counter = ZhipuTokenCounter(
            model_name="glm-4",
        )
        n_tokens = await counter.count([])
        self.assertEqual(n_tokens, 0)

    async def test_zhipu_token_counter_simple_fallback(self) -> None:
        """Test the simple fallback counting method."""
        counter = ZhipuTokenCounter(
            model_name="glm-4",
        )
        # 强制使用简单计数方法
        counter.encoding = None
        n_tokens = await counter.count(self.messages)
        self.assertIsInstance(n_tokens, int)
        self.assertGreater(n_tokens, 0)

    async def test_zhipu_token_counter_different_models(self) -> None:
        """Test the Zhipu AI token counter with different models."""
        models = ["glm-4", "glm-4-plus", "glm-4v", "glm-4-flash"]

        for model_name in models:
            with self.subTest(model=model_name):
                counter = ZhipuTokenCounter(model_name=model_name)
                n_tokens = await counter.count(self.messages)
                self.assertIsInstance(n_tokens, int)
                self.assertGreater(n_tokens, 0)

    async def test_zhipu_token_counter_tool_calls_in_messages(self) -> None:
        """Test token counting for messages with tool calls."""
        counter = ZhipuTokenCounter(
            model_name="glm-4",
        )

        # 只计算包含工具调用的消息
        tool_call_messages = [msg for msg in self.messages if msg.get("tool_calls")]
        n_tokens = await counter.count(tool_call_messages)
        self.assertIsInstance(n_tokens, int)
        self.assertGreater(n_tokens, 0)

    async def test_zhipu_token_counter_mixed_content(self) -> None:
        """Test token counting for messages with mixed content types."""
        mixed_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请分析这张图片"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,test"}},
                ],
            },
            {
                "role": "assistant",
                "content": "这是一张测试图片。",
                "tool_calls": [
                    {
                        "id": "test_call",
                        "type": "function",
                        "function": {
                            "name": "analyze_image",
                            "arguments": '{"description": "测试图片"}',
                        },
                    },
                ],
            },
        ]

        counter = ZhipuTokenCounter(model_name="glm-4v")
        n_tokens = await counter.count(mixed_messages, self.tools)
        self.assertIsInstance(n_tokens, int)
        self.assertGreater(n_tokens, 0)

