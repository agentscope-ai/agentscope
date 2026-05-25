# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OpenAI API model class."""
from typing import AsyncGenerator, Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch, AsyncMock
from pydantic import BaseModel

from agentscope.model import OpenAIChatModel, ChatResponse
from agentscope.message import TextBlock, ToolUseBlock, ThinkingBlock


class SampleModel(BaseModel):
    """Sample Pydantic model for testing structured output."""

    name: str
    age: int


class TestOpenAIChatModel(IsolatedAsyncioTestCase):
    """Test cases for OpenAIChatModel."""

    def test_init_default_params(self) -> None:
        """Test initialization with default parameters."""
        with patch("openai.AsyncClient") as mock_client:
            model = OpenAIChatModel(model_name="gpt-4", api_key="test_key")
            self.assertEqual(model.model_name, "gpt-4")
            self.assertTrue(model.stream)
            self.assertIsNone(model.reasoning_effort)
            self.assertEqual(model.generate_kwargs, {})
            mock_client.assert_called_once_with(
                api_key="test_key",
                organization=None,
            )

    def test_init_with_custom_params(self) -> None:
        """Test initialization with custom parameters."""
        generate_kwargs = {"temperature": 0.7, "max_tokens": 1000}
        client_kwargs = {"timeout": 30}
        with patch("openai.AsyncClient") as mock_client:
            model = OpenAIChatModel(
                model_name="gpt-4o",
                api_key="test_key",
                stream=False,
                reasoning_effort="high",
                organization="org-123",
                client_kwargs=client_kwargs,
                generate_kwargs=generate_kwargs,
            )
            self.assertEqual(model.model_name, "gpt-4o")
            self.assertFalse(model.stream)
            self.assertEqual(model.reasoning_effort, "high")
            self.assertEqual(model.generate_kwargs, generate_kwargs)
            mock_client.assert_called_once_with(
                api_key="test_key",
                organization="org-123",
                timeout=30,
            )

    async def test_call_with_regular_model(self) -> None:
        """Test calling a regular model."""
        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = OpenAIChatModel(
                model_name="gpt-4",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]
            mock_response = self._create_mock_response(
                "Hello! How can I help you?",
            )
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response,
            )

            result = await model(messages)
            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertEqual(call_args["model"], "gpt-4")
            self.assertEqual(call_args["messages"], messages)
            self.assertFalse(call_args["stream"])
            self.assertIsInstance(result, ChatResponse)
            expected_content = [
                TextBlock(type="text", text="Hello! How can I help you?"),
            ]
            self.assertEqual(result.content, expected_content)

    async def test_call_with_tools_integration(self) -> None:
        """Test full integration of tool calls."""
        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = OpenAIChatModel(
                model_name="gpt-4",
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
            mock_client.chat.completions.create = AsyncMock(
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

    async def test_call_with_reasoning_effort(self) -> None:
        """Test calling with reasoning effort enabled."""
        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = OpenAIChatModel(
                model_name="o3-mini",
                api_key="test_key",
                stream=False,
                reasoning_effort="high",
            )
            model.client = mock_client

            messages = [
                {"role": "user", "content": "Think about this problem"},
            ]
            mock_response = self._create_mock_response_with_reasoning(
                "Here's my analysis",
                "Let me analyze this step by step...",
            )
            mock_client.chat.completions.create = AsyncMock(
                return_value=mock_response,
            )
            result = await model(messages)

            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertEqual(call_args["reasoning_effort"], "high")
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
        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = OpenAIChatModel(
                model_name="gpt-4",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Generate a person"}]
            mock_response = self._create_mock_response_with_structured_data(
                {"name": "John", "age": 30},
            )
            mock_client.chat.completions.parse = AsyncMock(
                return_value=mock_response,
            )

            result = await model(messages, structured_model=SampleModel)
            call_args = mock_client.chat.completions.parse.call_args[1]
            self.assertEqual(call_args["response_format"], SampleModel)
            self.assertNotIn("tools", call_args)
            self.assertNotIn("tool_choice", call_args)
            self.assertNotIn("stream", call_args)
            self.assertIsInstance(result, ChatResponse)
            self.assertEqual(result.metadata, {"name": "John", "age": 30})

    async def test_streaming_response_processing(self) -> None:
        """Test processing of streaming response."""
        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = OpenAIChatModel(
                model_name="gpt-4",
                api_key="test_key",
                stream=True,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]
            stream_mock = self._create_stream_mock(
                [
                    {"content": "Hello"},
                    {"content": " there!"},
                ],
            )

            mock_client.chat.completions.create = AsyncMock(
                return_value=stream_mock,
            )
            result = await model(messages)

            call_args = mock_client.chat.completions.create.call_args[1]
            self.assertEqual(
                call_args["stream_options"],
                {"include_usage": True},
            )
            responses = []
            async for response in result:
                responses.append(response)

            self.assertEqual(len(responses), 2)
            final_response = responses[-1]
            expected_content = [TextBlock(type="text", text="Hello there!")]
            self.assertEqual(final_response.content, expected_content)

    async def test_streaming_tool_input_prefers_valid_final_json(self) -> None:
        """Test streaming tool input keeps the final valid JSON dict."""
        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = OpenAIChatModel(
                model_name="gpt-4",
                api_key="test_key",
                stream=True,
            )
            model.client = mock_client

            stream_mock = self._create_stream_mock(
                [
                    {
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "name": "score",
                                "arguments": '{"points": ',
                            },
                        ],
                    },
                    {
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "name": "score",
                                "arguments": "1}",
                            },
                        ],
                    },
                ],
            )

            mock_client.chat.completions.create = AsyncMock(
                return_value=stream_mock,
            )

            result = await model([{"role": "user", "content": "Score it"}])

            responses = []
            async for response in result:
                responses.append(response)

            final_response = responses[-1]
            expected_content = [
                ToolUseBlock(
                    type="tool_use",
                    id="call_123",
                    name="score",
                    input={"points": 1},
                    raw_input='{"points": 1}',
                ),
            ]
            self.assertEqual(final_response.content, expected_content)

    # Auxiliary methods - ensure all Mock objects have complete attributes
    def _create_mock_response(
        self,
        content: str = "",
        prompt_tokens: int = 10,
        completion_tokens: int = 20,
    ) -> Mock:
        """Create a standard mock response."""
        message = Mock()
        message.content = content
        message.reasoning_content = None
        message.tool_calls = []
        message.audio = None
        message.parsed = None

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

    def _create_mock_response_with_reasoning(
        self,
        content: str,
        reasoning_content: str,
    ) -> Mock:
        """Create a mock response with reasoning content."""
        response = self._create_mock_response(content)
        response.choices[0].message.reasoning_content = reasoning_content
        return response

    def _create_mock_response_with_structured_data(self, data: dict) -> Mock:
        """Create a mock response with structured data."""
        message = Mock()
        # `message.parsed` must be a real instance of the schema so that the
        # provider-agnostic ``isinstance(parsed, structured_model)`` guard
        # in ``__call__`` accepts the response.
        message.parsed = SampleModel(**data)
        message.content = None
        message.reasoning_content = None
        message.tool_calls = []

        choice = Mock()
        choice.message = message

        response = Mock()
        response.choices = [choice]
        response.usage = None

        return response

    async def test_structured_sync_fallback_on_validation_error(
        self,
    ) -> None:
        """``.parse()`` raising ``ValidationError`` (HTTP 200 + body that
        does not match the schema) must trigger the tool-call fallback.

        Regression for agentscope-ai/agentscope#1631: DashScope
        OpenAI-compat returns arbitrary JSON when messages contain the
        word ``json``; the OpenAI SDK then raises
        ``pydantic.ValidationError`` which the previous code did not
        catch, crashing ``ReActAgent.CompressionConfig``.
        """
        from pydantic import ValidationError

        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            model = OpenAIChatModel(
                model_name="qwen3.6-flash",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            # Construct a real ValidationError to mirror SDK behaviour.
            try:
                SampleModel.model_validate({})
            except ValidationError as exc:
                ve = exc
            mock_client.chat.completions.parse = AsyncMock(side_effect=ve)

            fallback_response = self._create_mock_response_with_tools(
                "",
                [
                    {
                        "id": "call_1",
                        "name": "SampleModel",
                        "arguments": '{"name": "Alice", "age": 7}',
                    },
                ],
            )
            mock_client.chat.completions.create = AsyncMock(
                return_value=fallback_response,
            )

            result = await model(
                [{"role": "user", "content": "x"}],
                structured_model=SampleModel,
            )

            self.assertTrue(model._structured_output_fallback)
            self.assertTrue(mock_client.chat.completions.create.called)
            self.assertIsInstance(result, ChatResponse)
            self.assertEqual(
                result.metadata,
                {"name": "Alice", "age": 7},
            )

    async def test_structured_sync_fallback_on_silent_mismatch(
        self,
    ) -> None:
        """``.parse()`` returning ``message.parsed=None`` (silent
        non-conforming body) must also trigger the tool-call fallback,
        not silently propagate ``metadata=None`` to the caller."""
        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            model = OpenAIChatModel(
                model_name="qwen3.6-flash",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client

            # `.parse()` returns successfully but parsed is None
            bad_response = self._create_mock_response("")
            bad_response.choices[0].message.parsed = None
            mock_client.chat.completions.parse = AsyncMock(
                return_value=bad_response,
            )

            fallback_response = self._create_mock_response_with_tools(
                "",
                [
                    {
                        "id": "call_1",
                        "name": "SampleModel",
                        "arguments": '{"name": "Bob", "age": 9}',
                    },
                ],
            )
            mock_client.chat.completions.create = AsyncMock(
                return_value=fallback_response,
            )

            result = await model(
                [{"role": "user", "content": "x"}],
                structured_model=SampleModel,
            )

            self.assertTrue(model._structured_output_fallback)
            self.assertTrue(mock_client.chat.completions.create.called)
            self.assertEqual(
                result.metadata,
                {"name": "Bob", "age": 9},
            )

    async def test_structured_stream_fallback_on_validation_error(
        self,
    ) -> None:
        """Streaming path: ``ValidationError`` raised during stream
        consumption must trigger transparent fallback to tool-call.
        """
        from pydantic import ValidationError

        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            model = OpenAIChatModel(
                model_name="qwen3.6-flash",
                api_key="test_key",
                stream=True,
            )
            model.client = mock_client

            try:
                SampleModel.model_validate({})
            except ValidationError as exc:
                ve = exc

            class _RaisingStream:
                """Mock that raises ValidationError mid-stream consumption,
                mirroring openai-python's stream parser behaviour when the
                accumulated body fails schema validation."""

                async def __aenter__(self) -> "_RaisingStream":
                    return self

                async def __aexit__(
                    self,
                    exc_type: Any,
                    exc_val: Any,
                    exc_tb: Any,
                ) -> None:
                    pass

                def __aiter__(self) -> "_RaisingStream":
                    return self

                async def __anext__(self) -> Any:
                    raise ve

            mock_client.chat.completions.stream = Mock(
                return_value=_RaisingStream(),
            )

            fallback_stream = self._create_stream_mock(
                [
                    {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "name": "SampleModel",
                                "arguments": '{"name":"C","age":3}',
                            },
                        ],
                    },
                ],
            )
            mock_client.chat.completions.create = AsyncMock(
                return_value=fallback_stream,
            )

            result = await model(
                [{"role": "user", "content": "x"}],
                structured_model=SampleModel,
            )

            chunks = []
            async for chunk in result:
                chunks.append(chunk)

            self.assertTrue(model._structured_output_fallback)
            self.assertTrue(mock_client.chat.completions.create.called)
            self.assertTrue(len(chunks) >= 1)
            # In streaming fallback the structured payload is carried on the
            # tool_use block (the tool name is the schema's tool name).
            tool_blocks = [
                b for b in chunks[-1].content if b.get("type") == "tool_use"
            ]
            self.assertEqual(len(tool_blocks), 1)
            self.assertEqual(
                tool_blocks[0]["input"],
                {"name": "C", "age": 3},
            )

    async def test_structured_via_tool_call_retries_on_tool_choice_400(
        self,
    ) -> None:
        """``_structured_via_tool_call`` must retry with
        ``tool_choice='auto'`` when the endpoint rejects forced
        ``tool_choice`` (e.g. DashScope thinking-mode models)."""
        import openai

        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            model = OpenAIChatModel(
                model_name="qwen3.6-flash",
                api_key="test_key",
                stream=False,
            )
            model.client = mock_client
            # Skip the response_format attempt; go straight to fallback.
            model._structured_output_fallback = True

            http_response = Mock()
            http_response.status_code = 400
            http_response.request = Mock()
            http_response.headers = {}
            bad_request = openai.BadRequestError(
                message=(
                    "tool_choice required is not supported in thinking " "mode"
                ),
                response=http_response,
                body=None,
            )

            success_response = self._create_mock_response_with_tools(
                "",
                [
                    {
                        "id": "call_1",
                        "name": "SampleModel",
                        "arguments": '{"name": "Dan", "age": 4}',
                    },
                ],
            )
            mock_client.chat.completions.create = AsyncMock(
                side_effect=[bad_request, success_response],
            )

            result = await model(
                [{"role": "user", "content": "x"}],
                structured_model=SampleModel,
            )

            self.assertEqual(
                mock_client.chat.completions.create.call_count,
                2,
            )
            # Second call must downgrade to tool_choice='auto'
            second_call_kwargs = (
                mock_client.chat.completions.create.call_args_list[1][1]
            )
            self.assertEqual(second_call_kwargs["tool_choice"], "auto")
            self.assertEqual(
                result.metadata,
                {"name": "Dan", "age": 4},
            )

    async def test_structured_via_tool_call_stream_retries_on_lazy_400(
        self,
    ) -> None:
        """Streaming tool-call fallback: when the endpoint surfaces a
        ``tool_choice``-related error lazily as ``openai.APIError`` during
        stream iteration (DashScope thinking mode), the wrapper must retry
        once with ``tool_choice='auto'`` before any chunk is yielded.
        """
        import openai

        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            model = OpenAIChatModel(
                model_name="qwen3.6-flash",
                api_key="test_key",
                stream=True,
            )
            model.client = mock_client
            # Skip response_format; go straight to tool-call fallback.
            model._structured_output_fallback = True

            http_request = Mock()

            class _LazyApiErrorStream:
                """Mirrors openai SDK behaviour where SSE error events
                surface as ``APIError`` during stream iteration."""

                async def __aenter__(self) -> "_LazyApiErrorStream":
                    return self

                async def __aexit__(
                    self,
                    exc_type: Any,
                    exc_val: Any,
                    exc_tb: Any,
                ) -> None:
                    pass

                def __aiter__(self) -> "_LazyApiErrorStream":
                    return self

                async def __anext__(self) -> Any:
                    raise openai.APIError(
                        message=(
                            "The tool_choice parameter does not support "
                            "being set to required or object in thinking "
                            "mode"
                        ),
                        request=http_request,
                        body=None,
                    )

            success_stream = self._create_stream_mock(
                [
                    {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "name": "SampleModel",
                                "arguments": '{"name":"Eve","age":5}',
                            },
                        ],
                    },
                ],
            )
            mock_client.chat.completions.create = AsyncMock(
                side_effect=[_LazyApiErrorStream(), success_stream],
            )

            result = await model(
                [{"role": "user", "content": "x"}],
                structured_model=SampleModel,
            )

            chunks = []
            async for chunk in result:
                chunks.append(chunk)

            self.assertEqual(
                mock_client.chat.completions.create.call_count,
                2,
            )
            second_call_kwargs = (
                mock_client.chat.completions.create.call_args_list[1][1]
            )
            self.assertEqual(second_call_kwargs["tool_choice"], "auto")
            tool_blocks = [
                b for b in chunks[-1].content if b.get("type") == "tool_use"
            ]
            self.assertEqual(len(tool_blocks), 1)
            self.assertEqual(
                tool_blocks[0]["input"],
                {"name": "Eve", "age": 5},
            )

    async def test_structured_via_tool_call_stream_retries_on_sync_400(
        self,
    ) -> None:
        """Streaming tool-call fallback: when the endpoint rejects
        forced ``tool_choice`` *synchronously* on
        ``client.chat.completions.create()`` (e.g. DashScope qwen3.6-plus
        / qwen3.6-flash), the wrapper must retry once with
        ``tool_choice='auto'`` before consuming the stream.
        """
        import openai

        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            model = OpenAIChatModel(
                model_name="qwen3.6-plus",
                api_key="test_key",
                stream=True,
            )
            model.client = mock_client
            model._structured_output_fallback = True

            http_response = Mock()
            http_response.status_code = 400
            http_response.request = Mock()
            http_response.headers = {}
            sync_400 = openai.BadRequestError(
                message=(
                    "The tool_choice parameter does not support being "
                    "set to required or object in thinking mode"
                ),
                response=http_response,
                body=None,
            )

            success_stream = self._create_stream_mock(
                [
                    {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "name": "SampleModel",
                                "arguments": '{"name":"Fay","age":6}',
                            },
                        ],
                    },
                ],
            )
            mock_client.chat.completions.create = AsyncMock(
                side_effect=[sync_400, success_stream],
            )

            result = await model(
                [{"role": "user", "content": "x"}],
                structured_model=SampleModel,
            )

            chunks = []
            async for chunk in result:
                chunks.append(chunk)

            self.assertEqual(
                mock_client.chat.completions.create.call_count,
                2,
            )
            second_call_kwargs = (
                mock_client.chat.completions.create.call_args_list[1][1]
            )
            self.assertEqual(second_call_kwargs["tool_choice"], "auto")
            tool_blocks = [
                b for b in chunks[-1].content if b.get("type") == "tool_use"
            ]
            self.assertEqual(len(tool_blocks), 1)
            self.assertEqual(
                tool_blocks[0]["input"],
                {"name": "Fay", "age": 6},
            )

    async def test_streaming_response_with_none_delta(self) -> None:
        """Test streaming response when a chunk has delta = None."""
        with patch("openai.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client

            model = OpenAIChatModel(
                model_name="gpt-4",
                api_key="test_key",
                stream=True,
            )
            model.client = mock_client

            messages = [{"role": "user", "content": "Hello"}]
            stream_mock = self._create_stream_mock(
                [
                    {"content": "Hello"},
                    None,  # simulate missing delta
                    {"content": " there!"},
                ],
            )

            mock_client.chat.completions.create = AsyncMock(
                return_value=stream_mock,
            )

            result = await model(messages)

            responses = []
            async for response in result:
                responses.append(response)

            # The None-delta chunk should not break streaming parsing.
            # We still expect the final aggregated text.
            final_response = responses[-1]
            expected_content = [TextBlock(type="text", text="Hello there!")]
            self.assertEqual(final_response.content, expected_content)

    def _create_stream_mock(self, chunks_data: list) -> Any:
        """Create a mock stream with proper async context management."""

        class MockStream:
            """Mock stream class."""

            def __init__(self, chunks_data: list) -> None:
                self.chunks_data = chunks_data
                self.index = 0

            async def __aenter__(self) -> "MockStream":
                return self

            async def __aexit__(
                self,
                exc_type: Any,
                exc_val: Any,
                exc_tb: Any,
            ) -> None:
                pass

            def __aiter__(self) -> "MockStream":
                return self

            async def __anext__(self) -> AsyncGenerator:
                if self.index >= len(self.chunks_data):
                    raise StopAsyncIteration
                chunk_data = self.chunks_data[self.index]
                self.index += 1

                choice = Mock()

                if chunk_data is None:
                    choice.delta = None
                else:
                    delta = Mock()
                    delta.content = chunk_data.get("content")
                    delta.reasoning_content = chunk_data.get(
                        "reasoning_content",
                    )

                    audio_mock = Mock()
                    audio_mock.__contains__ = lambda self, key: False
                    delta.audio = audio_mock
                    if "audio" in chunk_data:
                        delta.audio = chunk_data["audio"]
                    if "tool_calls" in chunk_data:
                        tool_call_mocks = []
                        for tc_data in chunk_data["tool_calls"]:
                            tc_mock = Mock()
                            tc_mock.id = tc_data["id"]
                            tc_mock.index = 0
                            tc_mock.function = Mock()
                            tc_mock.function.name = tc_data["name"]
                            tc_mock.function.arguments = tc_data["arguments"]
                            tool_call_mocks.append(tc_mock)
                        delta.tool_calls = tool_call_mocks
                    else:
                        delta.tool_calls = []

                    choice.delta = delta

                chunk = Mock()
                chunk.choices = [choice]
                chunk.usage = Mock()
                chunk.usage.prompt_tokens = 5
                chunk.usage.completion_tokens = 10
                return chunk

        return MockStream(chunks_data)
