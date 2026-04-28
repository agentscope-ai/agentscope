# -*- coding: utf-8 -*-
"""Unit tests for Groq API model class."""
from typing import AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch, AsyncMock, MagicMock
from pydantic import BaseModel

from agentscope.model import GroqChatModel, ChatResponse
from agentscope.message import TextBlock


class SampleModel(BaseModel):
    """Sample Pydantic model for testing structured output."""

    name: str
    age: int


class TestGroqChatModel(IsolatedAsyncioTestCase):
    """Test cases for GroqChatModel."""

    def test_init_default_params(self) -> None:
        """Test initialization with default parameters."""
        with patch("groq.AsyncGroq") as mock_client:
            model = GroqChatModel(model_name="llama-3.3-70b-versatile")
            self.assertEqual(model.model_name, "llama-3.3-70b-versatile")
            self.assertTrue(model.stream)
            self.assertTrue(model.stream_tool_parsing)
            self.assertEqual(model.generate_kwargs, {})
            mock_client.assert_called_once_with(api_key=None)

    def test_init_with_custom_params(self) -> None:
        """Test initialization with custom parameters."""
        with patch("groq.AsyncGroq") as mock_client:
            model = GroqChatModel(
                model_name="mixtral-8x7b-32768",
                api_key="test-key",
                stream=False,
                stream_tool_parsing=False,
                client_kwargs={"timeout": 30},
                generate_kwargs={"temperature": 0.7},
            )
            self.assertEqual(model.model_name, "mixtral-8x7b-32768")
            self.assertFalse(model.stream)
            self.assertFalse(model.stream_tool_parsing)
            self.assertEqual(
                model.generate_kwargs,
                {"temperature": 0.7},
            )
            mock_client.assert_called_once_with(
                api_key="test-key",
                timeout=30,
            )

    def test_init_import_error(self) -> None:
        """Test ImportError when groq package is not installed."""
        with patch.dict("sys.modules", {"groq": None}):
            with self.assertRaises(ImportError) as ctx:
                GroqChatModel(model_name="llama-3.3-70b-versatile")
            self.assertIn("groq", str(ctx.exception))

    async def test_call_non_streaming(self) -> None:
        """Test calling a model in non-streaming mode."""
        with patch("groq.AsyncGroq") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = GroqChatModel(
                model_name="llama-3.3-70b-versatile",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]
            mock_response = self._create_mock_response(
                content="Hello! How can I help you?",
            )
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response,
            )

            result = await model(messages)

            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertEqual(call_args["model"], "llama-3.3-70b-versatile")
            self.assertEqual(call_args["messages"], messages)
            self.assertFalse(call_args["stream"])
            self.assertIsInstance(result, ChatResponse)
            expected_content = [
                TextBlock(type="text", text="Hello! How can I help you?"),
            ]
            self.assertEqual(result.content, expected_content)

    async def test_call_with_tools(self) -> None:
        """Test calling with tool use."""
        with patch("groq.AsyncGroq") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = GroqChatModel(
                model_name="llama-3.3-70b-versatile",
                stream=False,
            )
            model.client = mock_client

            messages = [
                {"role": "user", "content": "What's the weather?"},
            ]
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

            tool_call_mock = MagicMock()
            tool_call_mock.id = "call_123"
            tool_call_mock.function.name = "get_weather"
            tool_call_mock.function.arguments = '{"location": "Beijing"}'

            mock_response = self._create_mock_response(
                content="Let me check the weather.",
                tool_calls=[tool_call_mock],
            )
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response,
            )

            result = await model(messages, tools=tools)

            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertIn("tools", call_args)
            self.assertEqual(call_args["tools"], tools)
            self.assertIsInstance(result, ChatResponse)

            # Check text block
            self.assertEqual(result.content[0]["type"], "text")
            self.assertEqual(
                result.content[0]["text"],
                "Let me check the weather.",
            )
            # Check tool use block
            self.assertEqual(result.content[1]["type"], "tool_use")
            self.assertEqual(result.content[1]["name"], "get_weather")
            self.assertEqual(
                result.content[1]["input"],
                {"location": "Beijing"},
            )

    async def test_call_with_tool_choice(self) -> None:
        """Test tool_choice parameter formatting."""
        with patch("groq.AsyncGroq") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = GroqChatModel(
                model_name="llama-3.3-70b-versatile",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Test"}]
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "my_tool",
                        "description": "A tool",
                        "parameters": {"type": "object"},
                    },
                },
            ]

            mock_response = self._create_mock_response(content="ok")
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response,
            )

            # Test with standard mode
            await model(messages, tools=tools, tool_choice="auto")
            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertEqual(call_args["tool_choice"], "auto")

            # Test with specific function name
            await model(messages, tools=tools, tool_choice="my_tool")
            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertEqual(
                call_args["tool_choice"],
                {"type": "function", "function": {"name": "my_tool"}},
            )

    async def test_call_with_structured_model(self) -> None:
        """Test structured output via response_format."""
        with patch("groq.AsyncGroq") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = GroqChatModel(
                model_name="llama-3.3-70b-versatile",
                stream=False,
            )
            model.client = mock_client

            messages = [
                {"role": "user", "content": "Generate a person"},
            ]
            mock_response = self._create_mock_response(
                content='{"name": "Alice", "age": 25}',
            )
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response,
            )

            result = await model(messages, structured_model=SampleModel)

            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertIn("response_format", call_args)
            self.assertEqual(
                call_args["response_format"]["type"],
                "json_object",
            )
            self.assertNotIn("tools", call_args)
            self.assertNotIn("tool_choice", call_args)

            self.assertIsInstance(result, ChatResponse)
            self.assertEqual(
                result.metadata,
                {"name": "Alice", "age": 25},
            )

    async def test_streaming_response(self) -> None:
        """Test processing of streaming response."""
        with patch("groq.AsyncGroq") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = GroqChatModel(
                model_name="llama-3.3-70b-versatile",
                stream=True,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]

            chunks = [
                self._create_stream_chunk(
                    content="Hello",
                    response_id="resp_1",
                ),
                self._create_stream_chunk(
                    content=" there!",
                    response_id="resp_1",
                ),
                self._create_final_stream_chunk(
                    prompt_tokens=5,
                    completion_tokens=3,
                    response_id="resp_1",
                ),
            ]

            mock_client.chat.completions.create = AsyncMock(
                return_value=self._create_async_generator(chunks),
            )
            result = await model(messages)

            responses = []
            async for response in result:
                responses.append(response)

            self.assertGreater(len(responses), 0)
            final = responses[-1]
            self.assertIsInstance(final, ChatResponse)

    async def test_invalid_messages_raises(self) -> None:
        """Test that invalid messages raise ValueError."""
        with patch("groq.AsyncGroq"):
            model = GroqChatModel(
                model_name="llama-3.3-70b-versatile",
                stream=False,
            )

            with self.assertRaises(ValueError):
                await model("not a list")

            with self.assertRaises(ValueError):
                await model([{"role": "user"}])

    async def test_usage_parsing(self) -> None:
        """Test that usage information is parsed correctly."""
        with patch("groq.AsyncGroq") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = GroqChatModel(
                model_name="llama-3.3-70b-versatile",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Hi"}]
            mock_response = self._create_mock_response(
                content="Hello!",
                prompt_tokens=5,
                completion_tokens=2,
            )
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response,
            )

            result = await model(messages)
            self.assertIsNotNone(result.usage)
            self.assertEqual(result.usage.input_tokens, 5)
            self.assertEqual(result.usage.output_tokens, 2)

    async def test_generate_kwargs_merged(self) -> None:
        """Test that generate_kwargs are merged into the API call."""
        with patch("groq.AsyncGroq") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = GroqChatModel(
                model_name="llama-3.3-70b-versatile",
                stream=False,
                generate_kwargs={"temperature": 0.5, "top_p": 0.9},
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Test"}]
            mock_response = self._create_mock_response(content="ok")
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response,
            )

            await model(messages, max_tokens=100)
            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertEqual(call_args["temperature"], 0.5)
            self.assertEqual(call_args["top_p"], 0.9)
            self.assertEqual(call_args["max_tokens"], 100)

    # ---- Auxiliary methods ----

    def _create_mock_response(
        self,
        content: str = "",
        tool_calls: list | None = None,
        prompt_tokens: int = 10,
        completion_tokens: int = 20,
    ) -> MagicMock:
        """Create a mock ChatCompletion response."""
        response = MagicMock()
        response.id = "chatcmpl-test123"

        choice = MagicMock()
        choice.message.content = content
        choice.message.tool_calls = tool_calls
        response.choices = [choice]

        response.usage.prompt_tokens = prompt_tokens
        response.usage.completion_tokens = completion_tokens

        return response

    def _create_stream_chunk(
        self,
        content: str = "",
        tool_calls: list | None = None,
        response_id: str = "chatcmpl-test123",
    ) -> MagicMock:
        """Create a mock stream chunk with content."""
        chunk = MagicMock()
        chunk.id = response_id
        chunk.usage = None

        choice = MagicMock()
        choice.delta.content = content
        choice.delta.tool_calls = tool_calls
        chunk.choices = [choice]

        return chunk

    def _create_final_stream_chunk(
        self,
        prompt_tokens: int = 10,
        completion_tokens: int = 20,
        response_id: str = "chatcmpl-test123",
    ) -> MagicMock:
        """Create a final stream chunk with usage info and no choices."""
        chunk = MagicMock()
        chunk.id = response_id
        chunk.choices = []

        chunk.usage.prompt_tokens = prompt_tokens
        chunk.usage.completion_tokens = completion_tokens

        return chunk

    async def _create_async_generator(
        self,
        items: list,
    ) -> AsyncGenerator:
        """Create an asynchronous generator from a list."""
        for item in items:
            yield item
