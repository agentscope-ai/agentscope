# -*- coding: utf-8 -*-
"""Unit tests for Zhipu AI model class."""
from typing import AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

from pydantic import BaseModel

from agentscope.message import TextBlock, ToolUseBlock
from agentscope.model import ZhipuChatModel, ChatResponse


class SampleModel(BaseModel):
    """Sample Pydantic model for testing structured output."""

    name: str
    age: int


class TestZhipuChatModel(IsolatedAsyncioTestCase):
    """Test cases for ZhipuChatModel."""

    def test_init_default_params(self) -> None:
        """Test initialization with default parameters."""
        with patch("zai.ZhipuAiClient") as mock_client:
            model = ZhipuChatModel(model_name="glm-4", api_key="test_key")
            self.assertEqual(model.model_name, "glm-4")
            self.assertTrue(model.stream)
            self.assertEqual(model.generate_kwargs, {})
            mock_client.assert_called_once_with(
                api_key="test_key",
                base_url=None,
            )

    def test_init_with_custom_params(self) -> None:
        """Test initialization with custom parameters."""
        generate_kwargs = {"temperature": 0.7, "max_tokens": 1000}
        with patch("zai.ZhipuAiClient") as mock_client:
            model = ZhipuChatModel(
                model_name="glm-4-plus",
                api_key="test_key",
                stream=False,
                base_url="https://custom.api.url",
                generate_kwargs=generate_kwargs,
                timeout=30,
            )
            self.assertEqual(model.model_name, "glm-4-plus")
            self.assertFalse(model.stream)
            self.assertEqual(model.generate_kwargs, generate_kwargs)
            mock_client.assert_called_once_with(
                api_key="test_key",
                base_url="https://custom.api.url",
                timeout=30,
            )

    async def test_call_with_regular_model(self) -> None:
        """Test calling a regular model."""
        with patch("zai.ZhipuAiClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            model = ZhipuChatModel(
                model_name="glm-4",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]
            mock_response = self._create_mock_response(
                "Hello! How can I help you?",
            )
            mock_client.chat.completions.create = Mock(
                return_value=mock_response,
            )

            result = await model(messages)
            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertEqual(call_args["model"], "glm-4")
            self.assertEqual(call_args["messages"], messages)
            self.assertFalse(call_args["stream"])
            self.assertIsInstance(result, ChatResponse)
            expected_content = [
                TextBlock(type="text", text="Hello! How can I help you?"),
            ]
            self.assertEqual(result.content, expected_content)

    async def test_call_with_tools_integration(self) -> None:
        """Test full integration of tool calls."""
        with patch("zai.ZhipuAiClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            model = ZhipuChatModel(
                model_name="glm-4",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "What's the weather?"}]

            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather info",
                        "parameters": {"type": "object"},
                    },
                },
            ]

            mock_response = self._create_mock_response_with_tools(
                "I'll check the weather for you.",
                [
                    {
                        "id": "call_123",
                        "name": "get_weather",
                        "arguments": '{"location": "Beijing"}',
                    },
                ],
            )
            mock_client.chat.completions.create = Mock(
                return_value=mock_response,
            )
            result = await model(messages, tools=tools, tool_choice="auto")
            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertIn("tools", call_args)
            self.assertEqual(call_args["tools"], tools)
            self.assertEqual(call_args["tool_choice"], "auto")
            self.assertEqual(len(result.content), 2)
            self.assertEqual(result.content[0]["type"], "text")
            self.assertEqual(result.content[0]["text"], "I'll check the weather for you.")
            self.assertEqual(result.content[1]["type"], "tool_use")
            self.assertEqual(result.content[1]["id"], "call_123")
            self.assertEqual(result.content[1]["name"], "get_weather")
            self.assertEqual(result.content[1]["input"], {"location": "Beijing"})

    async def test_call_with_structured_model_integration(self) -> None:
        """Test full integration of a structured model."""
        with patch("zai.ZhipuAiClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            model = ZhipuChatModel(
                model_name="glm-4",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Generate a person"}]
            mock_response = self._create_mock_response(
                '{"name": "John", "age": 30}',
            )
            mock_client.chat.completions.create = Mock(
                return_value=mock_response,
            )

            result = await model(messages, structured_model=SampleModel)
            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertIn("response_format", call_args)
            self.assertEqual(call_args["response_format"], SampleModel)
            self.assertNotIn("tools", call_args)
            self.assertNotIn("tool_choice", call_args)
            self.assertIsInstance(result, ChatResponse)
            self.assertEqual(result.metadata, {"name": "John", "age": 30})

    async def test_streaming_response_processing(self) -> None:
        """Test processing of streaming response."""
        with patch("zai.ZhipuAiClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            model = ZhipuChatModel(
                model_name="glm-4",
                api_key="test_key",
                stream=True,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]
            chunks = [
                self._create_mock_chunk(content="Hello", finish_reason=None),
                self._create_mock_chunk(content=" there!", finish_reason="stop"),
            ]

            mock_client.chat.completions.create = Mock(
                return_value=iter(chunks),
            )
            result = await model(messages)
            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertEqual(call_args["model"], "glm-4")
            self.assertEqual(call_args["messages"], messages)
            self.assertTrue(call_args["stream"])
            self.assertIsInstance(result, AsyncGenerator)

            # Collect all yielded responses
            responses = []
            async for res in result:
                responses.append(res)

            self.assertEqual(len(responses), 2)

            self.assertGreaterEqual(len(responses[0].content), 1)
            text_block_0 = None
            for block in responses[0].content:
                if block["type"] == "text":
                    text_block_0 = block
                    break
            self.assertIsNotNone(text_block_0)
            self.assertEqual(text_block_0["text"], "Hello")

            text_block_1 = None
            for block in responses[1].content:
                if block["type"] == "text":
                    text_block_1 = block
                    break
            self.assertIsNotNone(text_block_1)
            self.assertEqual(text_block_1["text"], "Hello there!")

    async def test_non_streaming_response_processing(self) -> None:
        """Test processing of non-streaming response."""
        with patch("zai.ZhipuAiClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            model = ZhipuChatModel(
                model_name="glm-4",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]
            mock_response = self._create_mock_response("Hello! Nice to meet you.")
            mock_client.chat.completions.create = Mock(
                return_value=mock_response,
            )

            result = await model(messages)
            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertEqual(call_args["model"], "glm-4")
            self.assertEqual(call_args["messages"], messages)
            self.assertFalse(call_args["stream"])
            self.assertIsInstance(result, ChatResponse)
            self.assertEqual(result.content[0]["text"], "Hello! Nice to meet you.")

    async def test_tool_call_response_processing(self) -> None:
        """Test processing of tool call response."""
        with patch("zai.ZhipuAiClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            model = ZhipuChatModel(
                model_name="glm-4",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "What's the weather in NY?"}]
            mock_response = self._create_mock_response_with_tools(
                "I'll check the weather for you.",
                [
                    {
                        "id": "call_abc123",
                        "name": "get_weather",
                        "arguments": '{"location": "New York"}',
                    },
                ],
            )
            mock_client.chat.completions.create = Mock(
                return_value=mock_response,
            )

            result = await model(messages)
            self.assertIsInstance(result, ChatResponse)
            self.assertEqual(len(result.content), 2)
            self.assertEqual(result.content[0]["type"], "text")
            self.assertEqual(
                result.content[0]["text"],
                "I'll check the weather for you.",
            )
            self.assertEqual(result.content[1]["type"], "tool_use")
            self.assertEqual(result.content[1]["name"], "get_weather")
            self.assertEqual(
                result.content[1]["input"],
                {"location": "New York"},
            )

    async def test_structured_output_response_processing(self) -> None:
        """Test processing of structured output response."""
        with patch("zai.ZhipuAiClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            model = ZhipuChatModel(
                model_name="glm-4",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Generate a person"}]
            mock_response = self._create_mock_response('{"name": "Alice", "age": 25}')
            mock_client.chat.completions.create = Mock(
                return_value=mock_response,
            )

            result = await model(messages, structured_model=SampleModel)
            self.assertIsInstance(result, ChatResponse)
            self.assertEqual(result.metadata, {"name": "Alice", "age": 25})

    def _create_mock_response(self, content: str) -> Mock:
        """Create a mock response for testing."""
        mock_choice = Mock()
        mock_choice.message.content = content
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"

        mock_response = Mock()
        mock_response.choices = [mock_choice]
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 20
        return mock_response

    def _create_mock_response_with_tools(
            self,
            content: str,
            tool_calls: list[dict],
    ) -> Mock:
        """Create a mock response with tool calls for testing."""
        mock_tool_calls = []
        for tool_call in tool_calls:
            mock_function = Mock()
            mock_function.name = tool_call["name"]
            mock_function.arguments = tool_call["arguments"]

            mock_tool_call = Mock()
            mock_tool_call.id = tool_call["id"]
            mock_tool_call.function = mock_function
            mock_tool_calls.append(mock_tool_call)

        mock_choice = Mock()
        mock_choice.message.content = content
        mock_choice.message.tool_calls = mock_tool_calls
        mock_choice.finish_reason = "tool_calls"

        mock_response = Mock()
        mock_response.choices = [mock_choice]
        mock_response.usage.prompt_tokens = 15
        mock_response.usage.completion_tokens = 25
        return mock_response

    def _create_mock_chunk(
            self,
            content: str = "",
            finish_reason: str | None = None,
    ) -> Mock:
        """Create a mock streaming chunk for testing."""
        mock_delta = Mock()
        mock_delta.content = content
        mock_delta.tool_calls = None
        # Ensure reasoning_content is not present or is None
        if hasattr(mock_delta, 'reasoning_content'):
            delattr(mock_delta, 'reasoning_content')

        mock_choice = Mock()
        mock_choice.delta = mock_delta
        mock_choice.finish_reason = finish_reason

        mock_chunk = Mock()
        mock_chunk.choices = [mock_choice]
        return mock_chunk
