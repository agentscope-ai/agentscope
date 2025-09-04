# -*- coding: utf-8 -*-
"""Unit tests for Ollama API model class."""
from typing import AsyncGenerator, Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch, AsyncMock
from pydantic import BaseModel

from agentscope.model import OllamaChatModel, ChatResponse
from agentscope.message import TextBlock, ToolUseBlock, ThinkingBlock


class OllamaMessageMock:
    """Mock class for Ollama message objects."""

    def __init__(
        self,
        content: str = "",
        thinking: str = "",
        tool_calls: list = None,
    ):
        self.content = content
        self.thinking = thinking
        self.tool_calls = tool_calls or []


class OllamaFunctionMock:
    """Mock class for Ollama function objects."""

    def __init__(self, name: str, arguments: dict = None):
        self.name = name
        self.arguments = arguments or {}


class OllamaToolCallMock:
    """Mock class for Ollama tool call objects."""

    def __init__(
        self,
        call_id: str = None,
        function: OllamaFunctionMock = None,
    ):
        self.id = call_id
        self.function = function


class OllamaResponseMock:
    """Mock class for Ollama response objects."""

    def __init__(
        self,
        message: OllamaMessageMock = None,
        done: bool = True,
        prompt_eval_count: int = 0,
        eval_count: int = 0,
    ) -> None:
        self.message = message or OllamaMessageMock()
        self.done = done
        self.prompt_eval_count = prompt_eval_count
        self.eval_count = eval_count

    def get(self, key: str, default: Any = None) -> Any:
        """Mock dict-like get method."""
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        """Mock dict-like contains method."""
        return hasattr(self, key)


class SampleModel(BaseModel):
    """Sample Pydantic model for testing structured output."""

    name: str
    age: int


class TestOllamaChatModel(IsolatedAsyncioTestCase):
    """Test cases for OllamaChatModel."""

    def test_init_default_params(self) -> None:
        """Test initialization with default parameters."""
        with patch("ollama.AsyncClient") as mock_client:
            model = OllamaChatModel(model_name="llama3.2")
            self.assertEqual(model.model_name, "llama3.2")
            self.assertFalse(model.stream)
            self.assertIsNone(model.options)
            self.assertEqual(model.keep_alive, "5m")
            self.assertIsNone(model.think)
            mock_client.assert_called_once_with(host=None)

    def test_init_with_custom_params(self) -> None:
        """Test initialization with custom parameters."""
        options = {"temperature": 0.7, "top_p": 0.9}
        with patch("ollama.AsyncClient") as mock_client:
            model = OllamaChatModel(
                model_name="qwen2.5",
                stream=True,
                options=options,
                keep_alive="10m",
                enable_thinking=True,
                host="http://localhost:11434",
                timeout=30,
            )
            self.assertEqual(model.model_name, "qwen2.5")
            self.assertTrue(model.stream)
            self.assertEqual(model.options, options)
            self.assertEqual(model.keep_alive, "10m")
            self.assertTrue(model.think)
            mock_client.assert_called_once_with(
                host="http://localhost:11434",
                timeout=30,
            )

    async def test_call_with_regular_model(self) -> None:
        """Test calling a regular model."""
        with patch("ollama.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = OllamaChatModel(model_name="llama3.2", stream=False)
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]
            mock_response = self._create_mock_response(
                "Hello! How can I help you?",
            )
            mock_client.chat = AsyncMock(return_value=mock_response)

            result = await model(messages)
            call_args = mock_client.chat.call_args[1]
            self.assertEqual(call_args["model"], "llama3.2")
            self.assertEqual(call_args["messages"], messages)
            self.assertFalse(call_args["stream"])
            self.assertIsInstance(result, ChatResponse)
            expected_content = [
                TextBlock(type="text", text="Hello! How can I help you?"),
            ]
            self.assertEqual(result.content, expected_content)

    async def test_call_with_tools_integration(self) -> None:
        """Test full integration of tool calls."""
        with patch("ollama.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = OllamaChatModel(model_name="llama3.2", stream=False)
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

            function_mock = OllamaFunctionMock(
                name="get_weather",
                arguments={"location": "Beijing"},
            )
            tool_call_mock = OllamaToolCallMock(
                call_id="call_123",
                function=function_mock,
            )
            message_mock = OllamaMessageMock(
                content="I'll check the weather for you.",
                tool_calls=[tool_call_mock],
            )
            mock_response = self._create_mock_response_with_message(
                message_mock,
            )

            mock_client.chat = AsyncMock(return_value=mock_response)
            result = await model(messages, tools=tools)

            call_args = mock_client.chat.call_args[1]
            self.assertIn("tools", call_args)
            self.assertEqual(call_args["tools"], tools)
            expected_content = [
                TextBlock(type="text", text="I'll check the weather for you."),
                ToolUseBlock(
                    type="tool_use",
                    id="0_get_weather",
                    name="get_weather",
                    input={"location": "Beijing"},
                ),
            ]
            self.assertEqual(result.content, expected_content)

    async def test_call_with_thinking_enabled(self) -> None:
        """Test calling with thinking functionality enabled."""
        with patch("ollama.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = OllamaChatModel(
                model_name="qwen2.5",
                stream=False,
                enable_thinking=True,
            )
            model.client = mock_client

            messages = [
                {"role": "user", "content": "Think about this problem"},
            ]
            message_mock = OllamaMessageMock(
                content="Here's my analysis",
                thinking="Let me analyze this step by step...",
            )
            mock_response = self._create_mock_response_with_message(
                message_mock,
            )

            mock_client.chat = AsyncMock(return_value=mock_response)
            result = await model(messages)

            call_args = mock_client.chat.call_args[1]
            self.assertTrue(call_args["think"])
            expected_content = [
                ThinkingBlock(
                    type="thinking",
                    thinking="Let me analyze this step by step...",
                ),
                TextBlock(type="text", text="Here's my analysis"),
            ]
            self.assertEqual(result.content, expected_content)

    async def test_call_with_structured_model_integration(self) -> None:
        """Test full integration of a structured model."""
        with patch("ollama.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = OllamaChatModel(model_name="llama3.2", stream=False)
            model.client = mock_client

            messages = [{"role": "user", "content": "Generate a person"}]
            mock_response = self._create_mock_response(
                '{"name": "John", "age": 30}',
            )
            mock_client.chat = AsyncMock(return_value=mock_response)

            result = await model(messages, structured_model=SampleModel)
            call_args = mock_client.chat.call_args[1]
            self.assertIn("format", call_args)
            self.assertEqual(
                call_args["format"],
                SampleModel.model_json_schema(),
            )
            self.assertIsInstance(result, ChatResponse)
            self.assertEqual(result.metadata, {"name": "John", "age": 30})
            expected_content = [
                TextBlock(type="text", text='{"name": "John", "age": 30}'),
            ]
            self.assertEqual(result.content, expected_content)

    async def test_streaming_response_processing(self) -> None:
        """Test processing of streaming response."""
        with patch("ollama.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = OllamaChatModel(model_name="llama3.2", stream=True)
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]
            chunks = [
                self._create_mock_chunk(content="Hello", done=False),
                self._create_mock_chunk(content=" there!", done=True),
            ]

            mock_client.chat = AsyncMock(
                return_value=self._create_async_generator(chunks),
            )
            result = await model(messages)
            responses = []
            async for response in result:
                responses.append(response)

            self.assertGreaterEqual(len(responses), 1)
            final_response = responses[-1]
            expected_content = [TextBlock(type="text", text="Hello there!")]
            self.assertEqual(final_response.content, expected_content)

    async def test_options_integration(self) -> None:
        """Test integration of options parameter."""
        with patch("ollama.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            options = {"temperature": 0.7, "top_p": 0.9}
            model = OllamaChatModel(
                model_name="llama3.2",
                stream=False,
                options=options,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Test"}]
            mock_response = self._create_mock_response("Test response")
            mock_client.chat = AsyncMock(return_value=mock_response)

            await model(messages, top_k=40)

            call_args = mock_client.chat.call_args[1]
            self.assertEqual(call_args["options"], options)
            self.assertEqual(call_args["keep_alive"], "5m")
            self.assertEqual(call_args["top_k"], 40)

    # Auxiliary methods
    def _create_mock_response(
        self,
        content: str = "",
        prompt_eval_count: int = 10,
        eval_count: int = 20,
    ) -> OllamaResponseMock:
        """Create a standard mock response."""
        message = OllamaMessageMock(content=content)
        return OllamaResponseMock(
            message=message,
            prompt_eval_count=prompt_eval_count,
            eval_count=eval_count,
        )

    def _create_mock_response_with_message(
        self,
        message: OllamaMessageMock,
        prompt_eval_count: int = 10,
        eval_count: int = 20,
    ) -> OllamaResponseMock:
        """Create a mock response with specific message."""
        return OllamaResponseMock(
            message=message,
            prompt_eval_count=prompt_eval_count,
            eval_count=eval_count,
        )

    def _create_mock_chunk(
        self,
        content: str = "",
        thinking: str = "",
        tool_calls: list = None,
        done: bool = True,
        prompt_eval_count: int = 5,
        eval_count: int = 10,
    ) -> OllamaResponseMock:
        """Create a mock chunk for streaming responses."""
        message = OllamaMessageMock(
            content=content,
            thinking=thinking,
            tool_calls=tool_calls or [],
        )
        return OllamaResponseMock(
            message=message,
            done=done,
            prompt_eval_count=prompt_eval_count,
            eval_count=eval_count,
        )

    async def _create_async_generator(self, items: list) -> AsyncGenerator:
        """Create an asynchronous generator."""
        for item in items:
            yield item
