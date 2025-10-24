# -*- coding: utf-8 -*-
# pylint: disable=too-many-statements
"""Unit tests for Trinity-RFT model class."""
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import Mock, AsyncMock

from agentscope.model import TrinityChatModel, ChatResponse
from agentscope.message import TextBlock


class TestTrinityModel(IsolatedAsyncioTestCase):
    """Test cases for TrinityModel."""

    async def test_init_with_trinity_client(self) -> None:
        """Test initialization with a valid OpenAI async client."""
        MODEL_NAME = "Qwen/Qwen3-8B"
        mock_client = Mock()
        mock_client.model_path = MODEL_NAME

        # test init
        model_1 = TrinityChatModel(
            openai_async_client=mock_client,
            enable_thinking=False,
            generate_kwargs={
                "temperature": 1.0,
                "top_k": 2,
            },
        )
        model_2 = TrinityChatModel(
            openai_async_client=mock_client,
            enable_thinking=True,
            generate_kwargs={
                "max_tokens": 500,
                "top_p": 0.9,
            },
        )
        self.assertEqual(model_1.model_name, MODEL_NAME)
        self.assertFalse(model_1.stream)
        self.assertIs(model_1.client, mock_client)
        self.assertEqual(model_2.model_name, MODEL_NAME)
        self.assertFalse(model_2.stream)
        self.assertIs(model_2.client, mock_client)

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

        result = await model_1(messages)
        call_args = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(call_args["model"], MODEL_NAME)
        self.assertEqual(call_args["messages"], messages)
        self.assertFalse(call_args["stream"])
        self.assertFalse(call_args["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(call_args["temperature"], 1.0)
        self.assertEqual(call_args["top_k"], 2)
        self.assertFalse("max_tokens" in call_args)
        self.assertFalse("top_p" in call_args)
        self.assertIsInstance(result, ChatResponse)
        expected_content = [
            TextBlock(type="text", text="Hi there!"),
        ]
        self.assertEqual(result.content, expected_content)

        result = await model_2(messages)
        call_args = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(call_args["model"], MODEL_NAME)
        self.assertEqual(call_args["messages"], messages)
        self.assertFalse(call_args["stream"])
        self.assertTrue(call_args["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(call_args["max_tokens"], 500)
        self.assertEqual(call_args["top_p"], 0.9)
        self.assertFalse("temperature" in call_args)
        self.assertFalse("top_k" in call_args)
        self.assertIsInstance(result, ChatResponse)
        expected_content = [
            TextBlock(type="text", text="Hi there!"),
        ]
        self.assertEqual(result.content, expected_content)
