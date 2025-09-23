# -*- coding: utf-8 -*-
"""The unittests for huggingface token counter."""
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.token import HuggingFaceTokenCounter


class TokenCounterTest(IsolatedAsyncioTestCase):
    """The unittests for the HuggingFace token counter."""

    async def asyncSetUp(self) -> None:
        """Set up the test case."""
        self.common_messages = [
            {"role": "user", "content": "Hi!"},
            {"role": "assistant", "content": "Hi! How can I help you today?"},
            {"role": "user", "content": "What is the capital of France?"},
            {
                "role": "assistant",
                "content": "The capital of France is Paris.",
            },
        ]

    async def test_huggingface_token_counter(self) -> None:
        """Test the HuggingFace token counter."""
        counter = HuggingFaceTokenCounter(
            pretrained_model_name_or_path="Qwen/Qwen3-8B",
            use_mirror=False,
        )

        res = await counter.count(
            self.common_messages,
        )
        self.assertEqual(res, 49)
