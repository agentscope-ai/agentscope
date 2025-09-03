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
            expected_content = [
                TextBlock(type="text", text="I'll check the weather for you."),
                ToolUseBlock(
                    type="tool_use",
                    id="call_123",
                    name="get_weather",
                    input={"location": "Beijing"},
                ),
            ]
            self.assertEqual(result.content, expected_content)

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
            expected_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "SampleModel",
                    "schema": SampleModel.model_json_schema(),
                },
            }
            self.assertEqual(call_args["response_format"], expected_format)
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

            responses = []
            async for response in result:
                responses.append(response)

            self.assertGreaterEqual(len(responses), 1)
            final_response = responses[-1]
            expected_content = [TextBlock(type="text", text="Hello there!")]
            self.assertEqual(final_response.content, expected_content)

    async def test_tool_choice_format_specific_function(self) -> None:
        """Test tool choice formatting for specific function."""
        with patch("zai.ZhipuAiClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            model = ZhipuChatModel(
                model_name="glm-4",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Test"}]
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "specific_tool",
                        "description": "A specific tool",
                        "parameters": {"type": "object"},
                    },
                },
            ]

            mock_response = self._create_mock_response("Test response")
            mock_client.chat.completions.create = Mock(
                return_value=mock_response,
            )

            await model(messages, tools=tools, tool_choice="specific_tool")
            call_args = mock_client.chat.completions.create.call_args[1]
            expected_tool_choice = {
                "type": "function",
                "function": {"name": "specific_tool"}
            }
            self.assertEqual(call_args["tool_choice"], expected_tool_choice)

    async def test_tool_choice_unsupported_modes(self) -> None:
        """Test tool choice handling for unsupported modes."""
        with patch("zai.ZhipuAiClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            model = ZhipuChatModel(
                model_name="glm-4",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Test"}]
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "test_tool",
                        "description": "A test tool",
                        "parameters": {"type": "object"},
                    },
                },
            ]

            mock_response = self._create_mock_response("Test response")
            mock_client.chat.completions.create = Mock(
                return_value=mock_response,
            )

            # Test "any" mode (should convert to "auto")
            with patch("agentscope.model._zhipu_model.logger") as mock_logger:
                await model(messages, tools=tools, tool_choice="any")
                call_args = mock_client.chat.completions.create.call_args[1]
                self.assertEqual(call_args["tool_choice"], "auto")
                mock_logger.warning.assert_called()

            # Test "required" mode (should convert to "auto")
            with patch("agentscope.model._zhipu_model.logger") as mock_logger:
                await model(messages, tools=tools, tool_choice="required")
                call_args = mock_client.chat.completions.create.call_args[1]
                self.assertEqual(call_args["tool_choice"], "auto")
                mock_logger.warning.assert_called()

    async def test_generate_kwargs_integration(self) -> None:
        """Test integration of generate_kwargs parameter."""
        with patch("zai.ZhipuAiClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            generate_kwargs = {"temperature": 0.7, "top_p": 0.9}
            model = ZhipuChatModel(
                model_name="glm-4",
                api_key="test_key",
                stream=False,
                generate_kwargs=generate_kwargs,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Test"}]
            mock_response = self._create_mock_response("Test response")
            mock_client.chat.completions.create = Mock(
                return_value=mock_response,
            )

            await model(messages, max_tokens=1000)

            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertEqual(call_args["temperature"], 0.7)
            self.assertEqual(call_args["top_p"], 0.9)
            self.assertEqual(call_args["max_tokens"], 1000)

    async def test_message_validation(self) -> None:
        """Test message validation."""
        with patch("zai.ZhipuAiClient") as mock_client_class:
            mock_client = Mock()
            mock_client_class.return_value = mock_client

            model = ZhipuChatModel(
                model_name="glm-4",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            # Test invalid messages type
            with self.assertRaises(ValueError) as context:
                await model("invalid_messages")
            self.assertIn("expected type `list`", str(context.exception))

            # Test missing required fields
            invalid_messages = [{"role": "user"}]  # missing content
            with self.assertRaises(ValueError) as context:
                await model(invalid_messages)
            self.assertIn("must contain a 'role' and 'content' key", str(context.exception))

    # Auxiliary methods
    def _create_mock_response(
        self,
        content: str = "",
        prompt_tokens: int = 10,
        completion_tokens: int = 20,
    ) -> Mock:
        """Create a standard mock response."""
        message = Mock()
        message.content = content
        message.tool_calls = []

        choice = Mock()
        choice.message = message

        response = Mock()
        response.choices = [choice]

        usage = Mock()
        usage.prompt_tokens = prompt_tokens
        usage.completion_tokens = completion_tokens
        response.usage = usage
        return response

    def _create_mock_response_with_tools(
        self,
        content: str,
        tool_calls: list,
    ) -> Mock:
        """Create a mock response with tool calls."""
        response = self._create_mock_response(content)
        tool_call_mocks = []
        for tool_call in tool_calls:
            tc_mock = Mock()
            tc_mock.id = tool_call["id"]
            tc_mock.function = Mock()
            tc_mock.function.name = tool_call["name"]
            tc_mock.function.arguments = tool_call["arguments"]
            tool_call_mocks.append(tc_mock)
        response.choices[0].message.tool_calls = tool_call_mocks
        return response

    def _create_mock_chunk(
        self,
        content: str = "",
        tool_calls: list = None,
        finish_reason: str = None,
        prompt_tokens: int = 5,
        completion_tokens: int = 10,
        reasoning_content: str = None,
    ) -> Mock:
        """Create a mock chunk for streaming responses."""
        delta = Mock()
        delta.content = content
        delta.tool_calls = []

        # Set reasoning_content to None by default to avoid mock issues
        if reasoning_content is not None:
            delta.reasoning_content = reasoning_content
        else:
            # Create a mock that returns None when accessed
            delta.reasoning_content = None

        if tool_calls:
            tool_call_mocks = []
            for tool_call in tool_calls:
                tc_mock = Mock()
                tc_mock.id = tool_call.get("id")
                tc_mock.index = tool_call.get("index", 0)
                tc_mock.function = Mock()
                tc_mock.function.name = tool_call.get("name")
                tc_mock.function.arguments = tool_call.get("arguments", "")
                tool_call_mocks.append(tc_mock)
            delta.tool_calls = tool_call_mocks

        choice = Mock()
        choice.delta = delta
        choice.finish_reason = finish_reason

        chunk = Mock()
        chunk.choices = [choice]

        usage = Mock()
        usage.prompt_tokens = prompt_tokens
        usage.completion_tokens = completion_tokens
        chunk.usage = usage

        return chunk

    async def _create_async_generator(self, items: list) -> AsyncGenerator:
        """Create an asynchronous generator."""
        for item in items:
            yield item

