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
                "content": "You are a helpful assistant.",
            },
            {
                "role": "user",
                "content": "What is the capital of France?",
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
            {
                "role": "assistant",
                "content": None,
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
                "role": "tool",
                "tool_call_id": "1",
                "content": "The capital of Japan is Tokyo.",
            },
            {
                "role": "assistant",
                "content": "The capital of Japan is Tokyo.",
            },
        ]

        self.multimodal_messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is in this image?",
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
                "content": "This is a 1x1 pixel transparent image.",
            },
        ]

        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_location",
                    "description": "Get user's location information",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {
                                "type": "string",
                                "description": "The city to get location information for",
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
            model_name="glm-4.5",
        )
        n_tokens = await counter.count(self.messages)
        self.assertIsInstance(n_tokens, int)
        self.assertGreater(n_tokens, 0)

    async def test_zhipu_token_counter_with_tools(self) -> None:
        """Test the Zhipu AI token counter with tools."""
        counter = ZhipuTokenCounter(
            model_name="glm-4.5",
        )
        n_tokens = await counter.count(self.messages, self.tools)
        self.assertIsInstance(n_tokens, int)
        self.assertGreater(n_tokens, 0)

        # Token count with tools should be greater than without tools
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
        # Force using simple counting method
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

        # Only count messages containing tool calls
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
                    {"type": "text", "text": "Please analyze this image"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,test"}},
                ],
            },
            {
                "role": "assistant",
                "content": "This is a test image.",
                "tool_calls": [
                    {
                        "id": "test_call",
                        "type": "function",
                        "function": {
                            "name": "analyze_image",
                            "arguments": '{"description": "test image"}',
                        },
                    },
                ],
            },
        ]

        counter = ZhipuTokenCounter(model_name="glm-4v")
        n_tokens = await counter.count(mixed_messages, self.tools)
        self.assertIsInstance(n_tokens, int)
        self.assertGreater(n_tokens, 0)
