# -*- coding: utf-8 -*-
"""Unit tests for Novita AI model class."""
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch, AsyncMock

from agentscope.model import NovitaChatModel, ChatResponse
from agentscope.message import TextBlock


class TestNovitaChatModel(IsolatedAsyncioTestCase):
    """Test cases for NovitaChatModel."""

    def test_init_default_params(self) -> None:
        """Test initialization with default parameters."""
        with patch("openai.AsyncClient") as mock_client:
            model = NovitaChatModel(model_name="meta-llama/llama-3-70b-instruct", api_key="test_key")
            self.assertEqual(model.model_name, "meta-llama/llama-3-70b-instruct")
            self.assertTrue(model.stream)
            mock_client.assert_called_once()
            call_args = mock_client.call_args[1]
            self.assertEqual(call_args["api_key"], "test_key")
            self.assertEqual(call_args["base_url"], "https://api.novita.ai/openai")

    async def test_call_novita(self) -> None:
        """Test calling Novita model."""
        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = NovitaChatModel(
                model_name="meta-llama/llama-3-70b-instruct",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]
            
            # Mock response
            mock_message = AsyncMock()
            mock_message.content = "Hello! I am Novita AI."
            mock_message.reasoning_content = None
            mock_message.tool_calls = []
            mock_message.audio = None
            mock_message.parsed = None

            mock_choice = AsyncMock()
            mock_choice.message = mock_message

            mock_response = AsyncMock()
            mock_response.choices = [mock_choice]
            mock_response.usage = AsyncMock()
            mock_response.usage.prompt_tokens = 10
            mock_response.usage.completion_tokens = 20

            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response,
            )

            result = await model(messages)
            self.assertIsInstance(result, ChatResponse)
            self.assertEqual(result.content, [TextBlock(type="text", text="Hello! I am Novita AI.")])
