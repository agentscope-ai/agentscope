# -*- coding: utf-8 -*-
"""Unit tests for Anthropic API model class."""
from typing import Any, AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch, AsyncMock
from pydantic import BaseModel

from agentscope.model import AnthropicChatModel, ChatResponse
from agentscope.message import TextBlock, ToolUseBlock, ThinkingBlock


class SampleModel(BaseModel):
    """Sample Pydantic model for testing structured output."""

    name: str
    age: int


class AnthropicMessageMock:
    """Mock class for Anthropic message objects."""

    def __init__(self, content: list = None, usage: dict = None):
        self.content = content or []
        self.usage = self._create_usage_mock(usage) if usage else None

    def _create_usage_mock(self, usage_data: dict) -> Mock:
        usage_mock = Mock()
        usage_mock.input_tokens = usage_data.get("input_tokens", 0)
        usage_mock.output_tokens = usage_data.get("output_tokens", 0)
        return usage_mock


class AnthropicContentBlockMock:
    """Mock class for Anthropic content blocks."""

    def __init__(self, block_type: str, **kwargs: Any) -> None:
        self.type = block_type
        for key, value in kwargs.items():
            setattr(self, key, value)


class AnthropicEventMock:
    """Mock class for Anthropic streaming events."""

    def __init__(self, event_type: str, **kwargs: Any) -> None:
        self.type = event_type
        for key, value in kwargs.items():
            setattr(self, key, value)


class TestAnthropicChatModel(IsolatedAsyncioTestCase):
    """Test cases for AnthropicChatModel."""

    def test_init_default_params(self) -> None:
        """Test initialization with default parameters."""
        with patch("anthropic.AsyncAnthropic") as mock_client:
            model = AnthropicChatModel(
                model_name="claude-3-sonnet-20240229",
                api_key="test_key",
            )
            self.assertEqual(model.model_name, "claude-3-sonnet-20240229")
            self.assertEqual(model.max_tokens, 2048)
            self.assertTrue(model.stream)
            self.assertIsNone(model.thinking)
            self.assertEqual(model.generate_kwargs, {})
            mock_client.assert_called_once_with(api_key="test_key")

    def test_init_with_custom_params(self) -> None:
        """Test initialization with custom parameters."""
        thinking_config = {"type": "enabled", "budget_tokens": 1024}
        generate_kwargs = {"temperature": 0.7, "top_p": 0.9}
        client_args = {"timeout": 30}

        with patch("anthropic.AsyncAnthropic") as mock_client:
            model = AnthropicChatModel(
                model_name="claude-3-opus-20240229",
                api_key="test_key",
                max_tokens=4096,
                stream=False,
                thinking=thinking_config,
                client_args=client_args,
                generate_kwargs=generate_kwargs,
            )
            self.assertEqual(model.model_name, "claude-3-opus-20240229")
            self.assertEqual(model.max_tokens, 4096)
            self.assertFalse(model.stream)
            self.assertEqual(model.thinking, thinking_config)
            self.assertEqual(model.generate_kwargs, generate_kwargs)
            mock_client.assert_called_once_with(api_key="test_key", timeout=30)

    async def test_call_with_regular_messages(self) -> None:
        """Test calling with regular messages."""
        with patch("anthropic.AsyncAnthropic") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = AnthropicChatModel(
                model_name="claude-3-sonnet-20240229",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]
            mock_response = AnthropicMessageMock(
                content=[
                    AnthropicContentBlockMock(
                        "text",
                        text="Hello! How can I help you?",
                    ),
                ],
                usage={"input_tokens": 10, "output_tokens": 20},
            )
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            result = await model(messages)
            call_args = mock_client.messages.create.call_args[1]
            self.assertEqual(call_args["model"], "claude-3-sonnet-20240229")
            self.assertEqual(call_args["max_tokens"], 2048)
            self.assertFalse(call_args["stream"])
            self.assertEqual(call_args["messages"], messages)
            self.assertIsInstance(result, ChatResponse)
            expected_content = [
                TextBlock(type="text", text="Hello! How can I help you?"),
            ]
            self.assertEqual(result.content, expected_content)
            self.assertEqual(result.usage.input_tokens, 10)
            self.assertEqual(result.usage.output_tokens, 20)

    async def test_call_with_system_message(self) -> None:
        """Test calling with system message extraction."""
        with patch("anthropic.AsyncAnthropic") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = AnthropicChatModel(
                model_name="claude-3-sonnet-20240229",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [
                {"role": "system", "content": "You are a helpful assistant"},
                {"role": "user", "content": "Hello"},
            ]
            mock_response = AnthropicMessageMock(
                content=[AnthropicContentBlockMock("text", text="Hi there!")],
                usage={"input_tokens": 15, "output_tokens": 5},
            )
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            await model(messages)

            call_args = mock_client.messages.create.call_args[1]
            self.assertEqual(
                call_args["system"],
                "You are a helpful assistant",
            )
            self.assertEqual(
                call_args["messages"],
                [
                    {"role": "user", "content": "Hello"},
                ],
            )

    async def test_call_with_thinking_enabled(self) -> None:
        """Test calling with thinking functionality enabled."""
        with patch("anthropic.AsyncAnthropic") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            thinking_config = {"type": "enabled", "budget_tokens": 1024}
            model = AnthropicChatModel(
                model_name="claude-3-sonnet-20240229",
                api_key="test_key",
                stream=False,
                thinking=thinking_config,
            )
            model.client = mock_client

            messages = [
                {"role": "user", "content": "Think about this problem"},
            ]
            thinking_block = AnthropicContentBlockMock(
                "thinking",
                thinking="Let me analyze this step by step...",
                signature="thinking_signature_123",
            )
            text_block = AnthropicContentBlockMock(
                "text",
                text="Here's my analysis",
            )
            mock_response = AnthropicMessageMock(
                content=[thinking_block, text_block],
                usage={"input_tokens": 20, "output_tokens": 40},
            )
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            result = await model(messages)

            call_args = mock_client.messages.create.call_args[1]
            self.assertEqual(call_args["thinking"], thinking_config)
            expected_thinking_block = ThinkingBlock(
                type="thinking",
                thinking="Let me analyze this step by step...",
            )
            expected_thinking_block["signature"] = "thinking_signature_123"
            expected_content = [
                expected_thinking_block,
                TextBlock(type="text", text="Here's my analysis"),
            ]
            self.assertEqual(result.content, expected_content)

    async def test_call_with_tools_integration(self) -> None:
        """Test full integration of tool calls."""
        with patch("anthropic.AsyncAnthropic") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = AnthropicChatModel(
                model_name="claude-3-sonnet-20240229",
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
            text_block = AnthropicContentBlockMock(
                "text",
                text="I'll check the weather",
            )
            tool_block = AnthropicContentBlockMock(
                "tool_use",
                id="tool_123",
                name="get_weather",
                input={"location": "Beijing"},
            )

            mock_response = AnthropicMessageMock(
                content=[text_block, tool_block],
                usage={"input_tokens": 25, "output_tokens": 15},
            )
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            result = await model(messages, tools=tools, tool_choice="auto")
            # Verify tool formatting
            call_args = mock_client.messages.create.call_args[1]
            expected_tools = [
                {
                    "name": "get_weather",
                    "description": "Get weather info",
                    "input_schema": {"type": "object"},
                },
            ]
            self.assertEqual(call_args["tools"], expected_tools)
            self.assertEqual(call_args["tool_choice"], {"type": "auto"})
            expected_content = [
                TextBlock(type="text", text="I'll check the weather"),
                ToolUseBlock(
                    type="tool_use",
                    id="tool_123",
                    name="get_weather",
                    input={"location": "Beijing"},
                ),
            ]
            self.assertEqual(result.content, expected_content)

    async def test_streaming_response_processing(self) -> None:
        """Test processing of streaming response."""
        with patch("anthropic.AsyncAnthropic") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = AnthropicChatModel(
                model_name="claude-3-sonnet-20240229",
                api_key="test_key",
                stream=True,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]
            events = [
                AnthropicEventMock(
                    "message_start",
                    message=Mock(usage=Mock(input_tokens=10, output_tokens=0)),
                ),
                AnthropicEventMock(
                    "content_block_delta",
                    index=0,
                    delta=Mock(type="text_delta", text="Hello"),
                ),
                AnthropicEventMock(
                    "content_block_delta",
                    index=0,
                    delta=Mock(type="text_delta", text=" there!"),
                ),
                AnthropicEventMock(
                    "message_delta",
                    usage=Mock(output_tokens=5),
                ),
            ]

            async def mock_stream() -> AsyncGenerator:
                for event in events:
                    yield event

            mock_client.messages.create = AsyncMock(return_value=mock_stream())
            result = await model(messages)
            responses = []
            async for response in result:
                responses.append(response)

            self.assertGreater(len(responses), 0)
            final_response = responses[-1]
            self.assertIsInstance(final_response, ChatResponse)
            expected_content = [
                TextBlock(type="text", text="Hello there!"),
            ]
            self.assertEqual(final_response.content, expected_content)

    async def test_generate_kwargs_integration(self) -> None:
        """Test integration of generate_kwargs."""
        with patch("anthropic.AsyncAnthropic") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            generate_kwargs = {"temperature": 0.7, "top_p": 0.9}
            model = AnthropicChatModel(
                model_name="claude-3-sonnet-20240229",
                api_key="test_key",
                stream=False,
                generate_kwargs=generate_kwargs,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Test"}]
            mock_response = AnthropicMessageMock(
                content=[
                    AnthropicContentBlockMock("text", text="Test response"),
                ],
                usage={"input_tokens": 5, "output_tokens": 10},
            )
            mock_client.messages.create = AsyncMock(return_value=mock_response)
            await model(messages, top_k=40)
            call_args = mock_client.messages.create.call_args[1]
            self.assertEqual(call_args["temperature"], 0.7)
            self.assertEqual(call_args["top_p"], 0.9)
            self.assertEqual(call_args["top_k"], 40)

    async def test_call_with_structured_model_integration(self) -> None:
        """Test full integration of structured model."""
        with patch("anthropic.AsyncAnthropic") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = AnthropicChatModel(
                model_name="claude-3-sonnet-20240229",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Generate a person"}]

            text_block = AnthropicContentBlockMock(
                "text",
                text="Here's a person",
            )
            tool_block = AnthropicContentBlockMock(
                "tool_use",
                id="tool_123",
                name="generate_structured_output",
                input={"name": "John", "age": 30},
            )

            mock_response = AnthropicMessageMock(
                content=[text_block, tool_block],
                usage={"input_tokens": 20, "output_tokens": 15},
            )

            mock_client.messages.create = AsyncMock(return_value=mock_response)
            result = await model(messages, structured_model=SampleModel)

            call_args = mock_client.messages.create.call_args[1]
            self.assertIn("tools", call_args)
            self.assertIn("tool_choice", call_args)
            expected_tools = [
                {
                    "name": "generate_structured_output",
                    "description": "Generate the required structured output"
                    " with this function",
                    "input_schema": {
                        "description": "Sample Pydantic model for testing "
                        "structured output.",
                        "properties": {
                            "name": {"type": "string"},
                            "age": {"type": "integer"},
                        },
                        "required": ["name", "age"],
                        "type": "object",
                    },
                },
            ]
            self.assertEqual(call_args["tools"], expected_tools)
            self.assertEqual(
                call_args["tool_choice"],
                {
                    "type": "tool",
                    "name": "generate_structured_output",
                },
            )
            self.assertIsInstance(result, ChatResponse)
            expected_content = [
                TextBlock(type="text", text="Here's a person"),
                ToolUseBlock(
                    type="tool_use",
                    id="tool_123",
                    name="generate_structured_output",
                    input={"name": "John", "age": 30},
                ),
            ]
            self.assertEqual(result.content, expected_content)
            self.assertEqual(result.metadata, {"name": "John", "age": 30})
