# -*- coding: utf-8 -*-
"""The unittests for huggingface token counter."""
import os
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.token import AnthropicTokenCounter


class AnthropicTokenCounterTest(IsolatedAsyncioTestCase):
    """The unittests for the Anthropic token counter."""

    async def asyncSetUp(self) -> None:
        """Set up the test case."""
        self.messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "You're a helpful assistant.",
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "What is the capital of France?",
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "BhdWRpbyBjb250ZW50",
                        },
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
                        "text": "What is the capital of Japan?",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "id": "1",
                        "type": "tool_use",
                        "name": "get_capital",
                        "input": {
                            "country": "Japan",
                        },
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "1",
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

    async def test_anthropic_token_counter(self) -> None:
        """Test the HuggingFace token counter."""

        if os.environ.get("ANTHROPIC_API_KEY"):
            counter = AnthropicTokenCounter(
                api_key=os.environ.get("ANTHROPIC_API_KEY"),
                model_name="claude-sonnet-4-20250514",
            )

            res = await counter.count(self.messages)
            self.assertEqual(res, 49)
