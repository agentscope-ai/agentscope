# -*- coding: utf-8 -*-
"""The unittests for char-based token counter."""
from unittest.async_case import IsolatedAsyncioTestCase


class CharTokenCounterTest(IsolatedAsyncioTestCase):
    """The unittests for char-based token counter."""

    async def test_count_tokens(self) -> None:
        """Test the count_tokens method."""
        from agentscope.token import CharTokenCounter

        counter = CharTokenCounter()

        messages = [
            {
                "role": "user",
                "content": "This is a test string.",
            },
            {
                "id": "1",
                "name": "test_tool",
                "type": "tool_use",
                "input": {
                    "param1": "value1",
                    "param2": "value2",
                },
            },
        ]
        num_tokens = await counter.count(messages)
        self.assertEqual(num_tokens, 157)

        messages = []
        num_tokens = await counter.count(messages)
        self.assertEqual(num_tokens, 0)
