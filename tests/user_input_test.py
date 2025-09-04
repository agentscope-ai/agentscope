# -*- coding: utf-8 -*-
"""The unittests for user input handling."""
from typing import Literal
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch, MagicMock

from pydantic import BaseModel, Field

from agentscope.agent import UserAgent


class UserInputTest(IsolatedAsyncioTestCase):
    """The user input test class."""

    @patch("builtins.input", side_effect=["Hi!", "sth", "apple"])
    async def test_user_terminal_input(self, mock_input: MagicMock) -> None:
        """Test the user input from terminal."""

        user_agent = UserAgent("Alice")

        class Choice(BaseModel):
            """The choice model."""

            thinking: str = Field(min_length=1, max_length=10)
            """The thinking"""
            decision: Literal["apple", "banana", "cherry"]

        msg_res = await user_agent(structured_model=Choice)

        self.assertEqual(
            msg_res.content,
            "Hi!",
        )

        self.assertEqual(
            msg_res.metadata,
            {
                "thinking": "sth",
                "decision": "apple",
            },
        )

        self.assertEqual(mock_input.call_count, 3)
