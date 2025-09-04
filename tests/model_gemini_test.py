# -*- coding: utf-8 -*-
"""Unit tests for Google Gemini API model class."""
from typing import AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch, AsyncMock
from pydantic import BaseModel

from agentscope.model import GeminiChatModel, ChatResponse
from agentscope.message import TextBlock, ToolUseBlock, ThinkingBlock


class GeminiResponseMock:
    """Mock class for Gemini response objects."""

    def __init__(
        self,
        text: str = "",
        function_calls: list = None,
        usage_metadata: dict = None,
        candidates: list = None,
    ):
        self.text = text
        self.function_calls = function_calls or []
        self.usage_metadata = (
            self._create_usage_mock(usage_metadata) if usage_metadata else None
        )
        self.candidates = candidates or []

    def _create_usage_mock(self, usage_data: dict) -> Mock:
        usage_mock = Mock()
        usage_mock.prompt_token_count = usage_data.get("prompt_token_count", 0)
        usage_mock.total_token_count = usage_data.get("total_token_count", 0)
        return usage_mock


class GeminiFunctionCallMock:
    """Mock class for Gemini function calls."""

    def __init__(self, call_id: str, name: str, args: dict = None):
        self.id = call_id
        self.name = name
        self.args = args or {}


class GeminiPartMock:
    """Mock class for Gemini content parts."""

    def __init__(self, text: str = "", thought: bool = False):
        self.text = text
        self.thought = thought


class GeminiCandidateMock:
    """Mock class for Gemini candidates."""

    def __init__(self, parts: list = None):
        self.content = Mock()
        self.content.parts = parts or []


class SampleModel(BaseModel):
    """Sample Pydantic model for testing structured output."""

    name: str
    age: int


class TestGeminiChatModel(IsolatedAsyncioTestCase):
    """Test cases for GeminiChatModel."""

    def test_init_default_params(self) -> None:
        """Test initialization with default parameters."""
        with patch("google.genai.Client") as mock_client:
            model = GeminiChatModel(
                model_name="gemini-2.5-flash",
                api_key="test_key",
            )
            self.assertEqual(model.model_name, "gemini-2.5-flash")
            self.assertTrue(model.stream)
            self.assertIsNone(model.thinking_config)
            self.assertEqual(model.generate_kwargs, {})
            mock_client.assert_called_once_with(api_key="test_key")

    def test_init_with_custom_params(self) -> None:
        """Test initialization with custom parameters."""
        thinking_config = {"include_thoughts": True, "thinking_budget": 1024}
        generate_kwargs = {"temperature": 0.7, "top_p": 0.9}
        client_args = {"timeout": 30}

        with patch("google.genai.Client") as mock_client:
            model = GeminiChatModel(
                model_name="gemini-2.5-pro",
                api_key="test_key",
                stream=False,
                thinking_config=thinking_config,
                client_args=client_args,
                generate_kwargs=generate_kwargs,
            )
            self.assertEqual(model.model_name, "gemini-2.5-pro")
            self.assertFalse(model.stream)
            self.assertEqual(model.thinking_config, thinking_config)
            self.assertEqual(model.generate_kwargs, generate_kwargs)
            mock_client.assert_called_once_with(api_key="test_key", timeout=30)

    async def test_call_with_regular_model(self) -> None:
        """Test calling a regular model."""
        with patch("google.genai.Client") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = GeminiChatModel(
                model_name="gemini-2.5-flash",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client
            messages = [{"role": "user", "content": "Hello"}]
            mock_response = self._create_mock_response(
                "Hello! How can I help you?",
            )
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response,
            )

            result = await model(messages)
            call_args = mock_client.aio.models.generate_content.call_args[1]
            self.assertEqual(call_args["model"], "gemini-2.5-flash")
            self.assertEqual(call_args["contents"], messages)
            self.assertIsInstance(result, ChatResponse)
            expected_content = [
                TextBlock(type="text", text="Hello! How can I help you?"),
            ]
            self.assertEqual(result.content, expected_content)

    async def test_call_with_tools_integration(self) -> None:
        """Test full integration of tool calls."""
        with patch("google.genai.Client") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = GeminiChatModel(
                model_name="gemini-2.5-flash",
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
                    GeminiFunctionCallMock(
                        call_id="call_123",
                        name="get_weather",
                        args={"location": "Beijing"},
                    ),
                ],
            )

            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response,
            )
            result = await model(messages, tools=tools, tool_choice="auto")

            call_args = mock_client.aio.models.generate_content.call_args[1]
            self.assertIn("tools", call_args["config"])
            self.assertIn("tool_config", call_args["config"])
            expected_tools = [
                {
                    "function_declarations": [
                        {
                            "name": "get_weather",
                            "description": "Get weather info",
                            "parameters": {"type": "object"},
                        },
                    ],
                },
            ]
            self.assertEqual(call_args["config"]["tools"], expected_tools)
            self.assertEqual(
                call_args["config"]["tool_config"],
                {
                    "function_calling_config": {"mode": "AUTO"},
                },
            )
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

    async def test_call_with_thinking_enabled(self) -> None:
        """Test calling with thinking functionality enabled."""
        with patch("google.genai.Client") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            thinking_config = {
                "include_thoughts": True,
                "thinking_budget": 1024,
            }
            model = GeminiChatModel(
                model_name="gemini-2.5-pro",
                api_key="test_key",
                stream=False,
                thinking_config=thinking_config,
            )
            model.client = mock_client

            messages = [
                {"role": "user", "content": "Think about this problem"},
            ]
            thinking_part = GeminiPartMock(
                text="Let me analyze this step by step...",
                thought=True,
            )
            candidate = GeminiCandidateMock(parts=[thinking_part])
            mock_response = self._create_mock_response_with_thinking(
                "Here's my analysis",
                candidates=[candidate],
            )
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response,
            )
            result = await model(messages)

            call_args = mock_client.aio.models.generate_content.call_args[1]
            self.assertEqual(
                call_args["config"]["thinking_config"],
                thinking_config,
            )
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
        with patch("google.genai.Client") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = GeminiChatModel(
                model_name="gemini-2.5-flash",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client
            messages = [{"role": "user", "content": "Generate a person"}]
            mock_response = self._create_mock_response(
                '{"name": "John", "age": 30}',
            )
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response,
            )

            result = await model(messages, structured_model=SampleModel)
            call_args = mock_client.aio.models.generate_content.call_args[1]
            self.assertEqual(
                call_args["config"]["response_mime_type"],
                "application/json",
            )
            self.assertEqual(
                call_args["config"]["response_schema"],
                SampleModel,
            )
            self.assertNotIn("tools", call_args["config"])
            self.assertNotIn("tool_config", call_args["config"])

            self.assertIsInstance(result, ChatResponse)
            self.assertEqual(result.metadata, {"name": "John", "age": 30})
            expected_content = [
                TextBlock(type="text", text='{"name": "John", "age": 30}'),
            ]
            self.assertEqual(result.content, expected_content)

    async def test_streaming_response_processing(self) -> None:
        """Test processing of streaming response."""
        with patch("google.genai.Client") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = GeminiChatModel(
                model_name="gemini-2.5-flash",
                api_key="test_key",
                stream=True,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]
            chunks = [
                self._create_mock_chunk(text="Hello"),
                self._create_mock_chunk(text=" there!"),
            ]

            mock_client.aio.models.generate_content_stream = AsyncMock(
                return_value=self._create_async_generator(chunks),
            )
            result = await model(messages)
            responses = []
            async for response in result:
                responses.append(response)

            self.assertEqual(len(responses), 2)
            final_response = responses[-1]
            expected_content = [
                TextBlock(type="text", text="Hello there!"),
            ]
            self.assertEqual(final_response.content, expected_content)

    async def test_generate_kwargs_integration(self) -> None:
        """Test integration of generate_kwargs."""
        with patch("google.genai.Client") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            generate_kwargs = {"temperature": 0.7, "top_p": 0.9}
            model = GeminiChatModel(
                model_name="gemini-2.5-flash",
                api_key="test_key",
                stream=False,
                generate_kwargs=generate_kwargs,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Test"}]
            mock_response = self._create_mock_response("Test response")
            mock_client.aio.models.generate_content = AsyncMock(
                return_value=mock_response,
            )

            await model(messages, top_k=40)

            call_args = mock_client.aio.models.generate_content.call_args[1]
            self.assertEqual(call_args["config"]["temperature"], 0.7)
            self.assertEqual(call_args["config"]["top_p"], 0.9)
            self.assertEqual(call_args["config"]["top_k"], 40)

    # Auxiliary methods
    def _create_mock_response(
        self,
        text: str = "",
        usage_metadata: dict = None,
    ) -> GeminiResponseMock:
        """Create a standard mock response."""
        return GeminiResponseMock(
            text=text,
            usage_metadata=usage_metadata
            or {"prompt_token_count": 10, "total_token_count": 30},
        )

    def _create_mock_response_with_tools(
        self,
        text: str,
        function_calls: list,
        usage_metadata: dict = None,
    ) -> GeminiResponseMock:
        """Create a mock response containing tool calls."""
        return GeminiResponseMock(
            text=text,
            function_calls=function_calls,
            usage_metadata=usage_metadata
            or {"prompt_token_count": 20, "total_token_count": 50},
        )

    def _create_mock_response_with_thinking(
        self,
        text: str,
        candidates: list = None,
        usage_metadata: dict = None,
    ) -> GeminiResponseMock:
        """Create a mock response with thinking parts."""
        return GeminiResponseMock(
            text=text,
            candidates=candidates or [],
            usage_metadata=usage_metadata
            or {"prompt_token_count": 15, "total_token_count": 35},
        )

    def _create_mock_chunk(
        self,
        text: str = "",
        function_calls: list = None,
        candidates: list = None,
        usage_metadata: dict = None,
    ) -> GeminiResponseMock:
        """Create a mock chunk for streaming responses."""
        return GeminiResponseMock(
            text=text,
            function_calls=function_calls or [],
            candidates=candidates or [],
            usage_metadata=usage_metadata
            or {
                "prompt_token_count": 5,
                "total_token_count": 15,
            },
        )

    async def _create_async_generator(self, items: list) -> AsyncGenerator:
        """Create an asynchronous generator."""
        for item in items:
            yield item
