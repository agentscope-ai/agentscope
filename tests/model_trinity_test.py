# -*- coding: utf-8 -*-
"""Unit tests for Trinity-RFT model class."""
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import Mock, AsyncMock

from agentscope.model import TrinityModel, ChatResponse
from agentscope.message import TextBlock


class TestTrinityModel(IsolatedAsyncioTestCase):
    """Test cases for TrinityModel."""

    async def test_init_with_trinity_client(self) -> None:
        """Test initialization with a valid OpenAI async client."""
        MODEL_NAME = "Qwen/Qwen3-8B"
        mock_client = Mock()
        mock_client.model_path = MODEL_NAME

        # test init
        model = TrinityModel(openai_async_client=mock_client)
        self.assertEqual(model.model_name, MODEL_NAME)
        self.assertFalse(model.stream)
        self.assertIs(model.client, mock_client)

        # create mock response
        messages = [{"role": "user", "content": "Hello"}]
        mock_message = Mock()
        mock_message.content = "Hi there!"
        mock_message.reasoning_content = None
        mock_message.tool_calls = []
        mock_message.audio = None
        mock_message.parsed = None
        mock_choice = Mock()
        mock_choice.message = mock_message
        mock_response = Mock()
        mock_response.choices = [mock_choice]
        mock_usage = Mock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 20
        mock_response.usage = mock_usage

        mock_client.chat.completions.create = AsyncMock(
            return_value=mock_response,
        )

        result = await model(messages)
        call_args = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(call_args["model"], MODEL_NAME)
        self.assertEqual(call_args["messages"], messages)
        self.assertFalse(call_args["stream"])
        self.assertIsInstance(result, ChatResponse)
        expected_content = [
            TextBlock(type="text", text="Hi there!"),
        ]
        self.assertEqual(result.content, expected_content)
