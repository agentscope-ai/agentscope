# -*- coding: utf-8 -*-
"""Unit tests for OpenAI Response API model class."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch, AsyncMock

from pydantic import BaseModel

from agentscope.model import ChatResponse
from agentscope.model._openai_response_model import OpenAIResponseModel
from agentscope.message import TextBlock, ToolUseBlock, ThinkingBlock


class SampleModel(BaseModel):
    """Sample Pydantic model for testing structured output."""

    name: str
    age: int


class TestOpenAIResponseModel(IsolatedAsyncioTestCase):
    """Test cases for OpenAIResponseModel."""

    def test_init_default_params(self) -> None:
        """Test initialization with default parameters."""
        with patch("openai.AsyncClient") as mock_client:
            model = OpenAIResponseModel(
                model_name="o3",
                api_key="test_key",
            )
            self.assertEqual(model.model_name, "o3")
            self.assertTrue(model.stream)
            self.assertIsNone(model.reasoning_effort)
            self.assertEqual(model.generate_kwargs, {})
            mock_client.assert_called_once_with(
                api_key="test_key",
                organization=None,
            )

    def test_init_with_custom_params(self) -> None:
        """Test initialization with custom parameters."""
        generate_kwargs = {"temperature": 0.7}
        client_kwargs = {"timeout": 30, "base_url": "https://custom.api/v1"}
        with patch("openai.AsyncClient") as mock_client:
            model = OpenAIResponseModel(
                model_name="o4-mini",
                api_key="test_key",
                stream=False,
                reasoning_effort="high",
                reasoning_summary="concise",
                organization="org-123",
                client_kwargs=client_kwargs,
                generate_kwargs=generate_kwargs,
            )
            self.assertFalse(model.stream)
            self.assertEqual(model.reasoning_effort, "high")
            self.assertEqual(model.reasoning_summary, "concise")
            mock_client.assert_called_once_with(
                api_key="test_key",
                organization="org-123",
                timeout=30,
                base_url="https://custom.api/v1",
            )

    async def test_non_streaming_text_response(self) -> None:
        """Test non-streaming with a simple text response."""
        with patch("openai.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client

            model = OpenAIResponseModel(
                model_name="o3",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            mock_response = self._create_mock_response(
                text="Hello! How can I help?",
            )
            mock_client.responses.create = AsyncMock(
                return_value=mock_response,
            )

            result = await model(
                [{"role": "user", "content": "Hello"}],
            )

            call_args = mock_client.responses.create.call_args[1]
            self.assertEqual(call_args["model"], "o3")
            self.assertFalse(call_args["stream"])
            self.assertIsInstance(result, ChatResponse)
            expected = [TextBlock(type="text", text="Hello! How can I help?")]
            self.assertEqual(result.content, expected)

    async def test_non_streaming_with_reasoning(self) -> None:
        """Test non-streaming response with reasoning/thinking."""
        with patch("openai.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client

            model = OpenAIResponseModel(
                model_name="o3",
                api_key="test_key",
                stream=False,
                reasoning_effort="high",
                reasoning_summary="concise",
            )
            model.client = mock_client

            mock_response = self._create_mock_response(
                text="The answer is 42.",
                reasoning_summary="Let me think step by step...",
            )
            mock_client.responses.create = AsyncMock(
                return_value=mock_response,
            )

            result = await model(
                [{"role": "user", "content": "Think hard"}],
            )

            call_args = mock_client.responses.create.call_args[1]
            self.assertEqual(
                call_args["reasoning"],
                {"effort": "high", "summary": "concise"},
            )
            expected = [
                ThinkingBlock(
                    type="thinking",
                    thinking="Let me think step by step...",
                ),
                TextBlock(type="text", text="The answer is 42."),
            ]
            self.assertEqual(result.content, expected)

    async def test_non_streaming_with_function_call(self) -> None:
        """Test non-streaming response with a function call."""
        with patch("openai.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client

            model = OpenAIResponseModel(
                model_name="o3",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            mock_response = self._create_mock_response(
                function_calls=[
                    {
                        "call_id": "call_abc",
                        "name": "get_weather",
                        "arguments": '{"city": "Beijing"}',
                    },
                ],
            )
            mock_client.responses.create = AsyncMock(
                return_value=mock_response,
            )

            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {"type": "object"},
                    },
                },
            ]
            result = await model(
                [{"role": "user", "content": "Weather?"}],
                tools=tools,
                tool_choice="auto",
            )

            call_args = mock_client.responses.create.call_args[1]
            self.assertEqual(call_args["tool_choice"], "auto")
            expected = [
                ToolUseBlock(
                    type="tool_use",
                    id="call_abc",
                    name="get_weather",
                    input={"city": "Beijing"},
                ),
            ]
            self.assertEqual(result.content, expected)

    async def test_streaming_text_response(self) -> None:
        """Test streaming with text deltas."""
        with patch("openai.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client

            model = OpenAIResponseModel(
                model_name="o3",
                api_key="test_key",
                stream=True,
            )
            model.client = mock_client

            stream = self._create_stream_mock(
                [
                    {"type": "response.output_text.delta", "delta": "Hello"},
                    {"type": "response.output_text.delta", "delta": " world"},
                    {
                        "type": "response.completed",
                        "input_tokens": 10,
                        "output_tokens": 5,
                    },
                ],
            )
            mock_client.responses.create = AsyncMock(return_value=stream)

            result = await model(
                [{"role": "user", "content": "Hi"}],
            )

            responses = []
            async for resp in result:
                responses.append(resp)

            final = responses[-1]
            expected = [TextBlock(type="text", text="Hello world")]
            self.assertEqual(final.content, expected)
            self.assertIsNotNone(final.usage)

    async def test_streaming_with_reasoning(self) -> None:
        """Test streaming with reasoning summary deltas."""
        with patch("openai.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client

            model = OpenAIResponseModel(
                model_name="o3",
                api_key="test_key",
                stream=True,
                reasoning_effort="high",
            )
            model.client = mock_client

            stream = self._create_stream_mock(
                [
                    {
                        "type": "response.reasoning_summary_text.delta",
                        "delta": "Thinking...",
                    },
                    {
                        "type": "response.output_text.delta",
                        "delta": "Answer",
                    },
                    {
                        "type": "response.completed",
                        "input_tokens": 10,
                        "output_tokens": 20,
                    },
                ],
            )
            mock_client.responses.create = AsyncMock(return_value=stream)

            result = await model(
                [{"role": "user", "content": "Think"}],
            )

            responses = []
            async for resp in result:
                responses.append(resp)

            final = responses[-1]
            self.assertEqual(final.content[0]["type"], "thinking")
            self.assertEqual(final.content[0]["thinking"], "Thinking...")
            self.assertEqual(final.content[1]["type"], "text")
            self.assertEqual(final.content[1]["text"], "Answer")

    async def test_streaming_function_call(self) -> None:
        """Test streaming with function call events."""
        with patch("openai.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client

            model = OpenAIResponseModel(
                model_name="o3",
                api_key="test_key",
                stream=True,
            )
            model.client = mock_client

            stream = self._create_stream_mock(
                [
                    {
                        "type": "response.output_item.added",
                        "item_id": "item_1",
                        "item_type": "function_call",
                        "call_id": "call_xyz",
                        "name": "get_weather",
                    },
                    {
                        "type": "response.function_call_arguments.delta",
                        "item_id": "item_1",
                        "delta": '{"city": "Beijing"}',
                    },
                    {
                        "type": "response.completed",
                        "input_tokens": 10,
                        "output_tokens": 15,
                    },
                ],
            )
            mock_client.responses.create = AsyncMock(return_value=stream)

            result = await model(
                [{"role": "user", "content": "Weather?"}],
            )

            responses = []
            async for resp in result:
                responses.append(resp)

            final = responses[-1]
            tool_blocks = [b for b in final.content if b["type"] == "tool_use"]
            self.assertEqual(len(tool_blocks), 1)
            self.assertEqual(tool_blocks[0]["name"], "get_weather")
            self.assertEqual(tool_blocks[0]["input"], {"city": "Beijing"})

    async def test_non_streaming_structured_model(self) -> None:
        """Test non-streaming with structured_model."""
        with patch("openai.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client

            model = OpenAIResponseModel(
                model_name="o3",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            mock_response = self._create_mock_response(
                text='{"name": "Alice", "age": 30}',
            )
            mock_client.responses.create = AsyncMock(
                return_value=mock_response,
            )

            result = await model(
                [{"role": "user", "content": "Generate a person"}],
                structured_model=SampleModel,
            )

            call_args = mock_client.responses.create.call_args[1]
            self.assertIn("text", call_args)
            self.assertEqual(
                call_args["text"]["format"]["type"],
                "json_schema",
            )
            self.assertEqual(
                call_args["text"]["format"]["name"],
                "SampleModel",
            )
            self.assertNotIn("tools", call_args)
            self.assertNotIn("tool_choice", call_args)
            self.assertIsInstance(result, ChatResponse)
            self.assertEqual(
                result.metadata,
                {"name": "Alice", "age": 30},
            )

    async def test_streaming_structured_model(self) -> None:
        """Test streaming with structured_model."""
        with patch("openai.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client

            model = OpenAIResponseModel(
                model_name="o3",
                api_key="test_key",
                stream=True,
            )
            model.client = mock_client

            stream = self._create_stream_mock(
                [
                    {
                        "type": "response.output_text.delta",
                        "delta": '{"name": "Bob",',
                    },
                    {
                        "type": "response.output_text.delta",
                        "delta": ' "age": 25}',
                    },
                    {
                        "type": "response.completed",
                        "input_tokens": 5,
                        "output_tokens": 10,
                    },
                ],
            )
            mock_client.responses.create = AsyncMock(return_value=stream)

            result = await model(
                [{"role": "user", "content": "Generate a person"}],
                structured_model=SampleModel,
            )

            responses = []
            async for resp in result:
                responses.append(resp)

            final = responses[-1]
            self.assertEqual(
                final.metadata,
                {"name": "Bob", "age": 25},
            )

    # ------------------------------------------------------------------
    # Mock helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _create_mock_response(
        text: str = "",
        reasoning_summary: str = "",
        function_calls: list | None = None,
        input_tokens: int = 10,
        output_tokens: int = 20,
    ) -> Mock:
        """Create a mock non-streaming Response object."""
        output_items = []

        if reasoning_summary:
            reasoning_item = Mock()
            reasoning_item.type = "reasoning"
            summary_obj = Mock()
            summary_obj.text = reasoning_summary
            reasoning_item.summary = [summary_obj]
            output_items.append(reasoning_item)

        if text:
            msg_item = Mock()
            msg_item.type = "message"
            text_part = Mock()
            text_part.type = "output_text"
            text_part.text = text
            msg_item.content = [text_part]
            output_items.append(msg_item)

        for fc in function_calls or []:
            fc_item = Mock()
            fc_item.type = "function_call"
            fc_item.call_id = fc["call_id"]
            fc_item.id = f"fc_{fc['call_id']}"
            fc_item.name = fc["name"]
            fc_item.arguments = fc["arguments"]
            output_items.append(fc_item)

        response = Mock()
        response.output = output_items
        response.id = "resp_test"

        usage = Mock()
        usage.input_tokens = input_tokens
        usage.output_tokens = output_tokens
        response.usage = usage

        return response

    @staticmethod
    def _create_stream_mock(events_data: list) -> Any:
        """Create a mock async event stream for the Response API."""

        class MockResponseStream:
            """Mock stream that yields Response API events."""

            def __init__(self, events_data: list) -> None:
                self.events_data = events_data
                self.index = 0

            def __aiter__(self) -> "MockResponseStream":
                return self

            async def __anext__(self) -> Any:
                if self.index >= len(self.events_data):
                    raise StopAsyncIteration
                data = self.events_data[self.index]
                self.index += 1

                event = Mock()
                event.type = data["type"]

                if data["type"] == "response.output_text.delta":
                    event.delta = data["delta"]
                    event.response = None

                elif data["type"] == ("response.reasoning_summary_text.delta"):
                    event.delta = data["delta"]
                    event.response = None

                elif data["type"] == "response.output_item.added":
                    item = Mock()
                    item.type = data.get("item_type", "message")
                    item.id = data.get("item_id", "")
                    item.call_id = data.get("call_id")
                    item.name = data.get("name", "")
                    event.item = item
                    event.response = None

                elif data["type"] == (
                    "response.function_call_arguments.delta"
                ):
                    event.item_id = data["item_id"]
                    event.delta = data["delta"]
                    event.response = None

                elif data["type"] == "response.completed":
                    resp = Mock()
                    resp.id = "resp_completed"
                    usage = Mock()
                    usage.input_tokens = data.get("input_tokens", 0)
                    usage.output_tokens = data.get("output_tokens", 0)
                    resp.usage = usage
                    event.response = resp

                else:
                    event.response = None

                return event

        return MockResponseStream(events_data)
