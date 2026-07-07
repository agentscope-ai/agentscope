# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OpenAIResponseModel with mocked API responses.

Tests cover both non-streaming and streaming modes.
OpenAI Responses API uses event-based streaming with response.completed.
"""
from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from utils import AnyString

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import OpenAIResponseModel
from agentscope.credential import OpenAICredential
from agentscope.tool import ToolChoice

A = AnyString()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return OpenAIResponseModel(
        credential=OpenAICredential(api_key="test"),
        model="o4-mini",
        stream=stream,
        context_size=200_000,
    )


def _mock_completion(
    text: Any = None,
    function_calls: Any = None,
    reasoning_summary: Any = None,
    reasoning_id: str = "rs_test123",
    response_id: str = "resp-openai-1",
) -> MagicMock:
    """Build a mock non-streaming Responses API response."""
    output = []

    if reasoning_summary:
        reasoning_item = MagicMock()
        reasoning_item.type = "reasoning"
        reasoning_item.id = reasoning_id
        summary_mock = MagicMock()
        summary_mock.text = reasoning_summary
        reasoning_item.summary = [summary_mock]
        output.append(reasoning_item)

    if text:
        msg_item = MagicMock()
        msg_item.type = "message"
        part = MagicMock()
        part.type = "output_text"
        part.text = text
        msg_item.content = [part]
        output.append(msg_item)

    if function_calls:
        for fc in function_calls:
            fc_item = MagicMock()
            fc_item.type = "function_call"
            fc_item.id = fc["id"]
            fc_item.call_id = fc["call_id"]
            fc_item.name = fc["name"]
            fc_item.arguments = fc["arguments"]
            output.append(fc_item)

    resp = MagicMock()
    resp.id = response_id
    resp.output = output
    resp.usage = MagicMock()
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 5
    resp.usage.input_tokens_details = None
    return resp


def _make_event(event_type: str, **kwargs: Any) -> MagicMock:
    """Build a mock Responses API streaming event."""
    event = MagicMock()
    event.type = event_type
    for key, val in kwargs.items():
        setattr(event, key, val)
    # Default: no response attribute
    if "response" not in kwargs:
        event.response = None
    return event


class _MockAsyncEventStream:
    """Mock async iterator over Response events."""

    def __init__(self, events: list) -> None:
        self._events = events
        self._index = 0

    def __aiter__(self) -> "_MockAsyncEventStream":
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event


# ---------------------------------------------------------------------------
# Non-streaming tests
# ---------------------------------------------------------------------------


class TestOpenAIResponseNonStream(IsolatedAsyncioTestCase):
    """Tests for OpenAIResponseModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)

    @patch("openai.AsyncClient")
    async def test_text_response(self, mock_client_cls: MagicMock) -> None:
        """Non-stream text response returns a single ChatResponse."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello!"),
        )
        mock_client_cls.return_value.responses.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (True, [TextBlock.model_construct(id=A, text="Hello!")]),
        )
        self.assertEqual(result.id, "resp-openai-1")

    @patch("openai.AsyncClient")
    async def test_tool_call_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Parsing a tool-call response creates a ToolCallBlock with
        call_id."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                function_calls=[
                    {
                        "id": "fc_abc",
                        "call_id": "call-1",
                        "name": "get_weather",
                        "arguments": '{"city":"BJ"}',
                    },
                ],
            ),
        )
        mock_client_cls.return_value.responses.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ToolCallBlock(
                        id="fc_abc",
                        call_id="call-1",
                        name="get_weather",
                        input='{"city":"BJ"}',
                    ),
                ],
            ),
        )

    @patch("openai.AsyncClient")
    async def test_reasoning_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Non-stream reasoning summary plus text returns both block types."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                reasoning_summary="Thinking step...",
                text="Answer",
                reasoning_id="rs_abc999",
            ),
        )
        mock_client_cls.return_value.responses.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        thinking="Thinking step...",
                        reasoning_item_id="rs_abc999",
                    ),
                    TextBlock.model_construct(id=A, text="Answer"),
                ],
            ),
        )


class TestOpenAIResponseModelParameters(unittest.TestCase):
    """Tests for OpenAIResponseModel.Parameters."""

    def test_thinking_enable_stored_on_model(self) -> None:
        """thinking_enable is accessible through model.parameters."""
        model = OpenAIResponseModel(
            credential=OpenAICredential(api_key="test"),
            model="o4-mini",
            stream=False,
            context_size=200_000,
            parameters=OpenAIResponseModel.Parameters(thinking_enable=True),
        )
        self.assertTrue(model.parameters.thinking_enable)

    def test_reasoning_effort_stored_on_model(self) -> None:
        """reasoning_effort is accessible through model.parameters."""
        model = OpenAIResponseModel(
            credential=OpenAICredential(api_key="test"),
            model="o4-mini",
            stream=False,
            context_size=200_000,
            parameters=OpenAIResponseModel.Parameters(
                reasoning_effort="high",
            ),
        )
        self.assertEqual(model.parameters.reasoning_effort, "high")


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestOpenAIResponseStream(IsolatedAsyncioTestCase):
    """Tests for OpenAIResponseModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)

    @patch("openai.AsyncClient")
    async def test_stream_text(self, mock_client_cls: MagicMock) -> None:
        """Stream text yields deltas then final with full content."""
        completed_resp = MagicMock()
        completed_resp.id = "resp-1"
        completed_resp.output = []
        completed_resp.usage = MagicMock()
        completed_resp.usage.input_tokens = 10
        completed_resp.usage.output_tokens = 5
        completed_resp.usage.input_tokens_details = None

        events = [
            _make_event(
                "response.output_text.delta",
                delta="Hello",
                response=MagicMock(id="resp-1"),
            ),
            _make_event(
                "response.output_text.delta",
                delta=" world",
            ),
            _make_event("response.completed", response=completed_resp),
        ]
        mock_create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )
        mock_client_cls.return_value.responses.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (False, [TextBlock.model_construct(id=A, text="Hello")]),
                (False, [TextBlock.model_construct(id=A, text=" world")]),
                (True, [TextBlock.model_construct(id=A, text="Hello world")]),
            ],
        )

    @patch("openai.AsyncClient")
    async def test_stream_reasoning_and_text(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream reasoning and text deltas then final with
        reasoning_item_id."""
        reasoning_item = MagicMock()
        reasoning_item.type = "reasoning"
        reasoning_item.id = "rs_123"

        completed_resp = MagicMock()
        completed_resp.id = "resp-2"
        completed_resp.output = [reasoning_item]
        completed_resp.usage = MagicMock()
        completed_resp.usage.input_tokens = 10
        completed_resp.usage.output_tokens = 5
        completed_resp.usage.input_tokens_details = None

        events = [
            _make_event(
                "response.reasoning_summary_text.delta",
                delta="Thinking",
                response=MagicMock(id="resp-2"),
            ),
            _make_event(
                "response.output_text.delta",
                delta="Answer",
            ),
            _make_event("response.completed", response=completed_resp),
        ]
        mock_create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )
        mock_client_cls.return_value.responses.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [ThinkingBlock.model_construct(id=A, thinking="Thinking")],
                ),
                (False, [TextBlock.model_construct(id=A, text="Answer")]),
                # ``reasoning_item_id`` is only known at
                # ``response.completed``; it is emitted as a dedicated
                # carrier delta chunk (empty thinking text) that the base
                # accumulator merges onto the existing ``ThinkingBlock``.
                (
                    False,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            thinking="",
                            reasoning_item_id="rs_123",
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            thinking="Thinking",
                            reasoning_item_id="rs_123",
                        ),
                        TextBlock.model_construct(id=A, text="Answer"),
                    ],
                ),
            ],
        )

    @patch("openai.AsyncClient")
    async def test_stream_function_call(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream function-call events yield deltas then final
        ToolCallBlock."""
        fc_item = MagicMock()
        fc_item.type = "function_call"
        fc_item.id = "fc_1"
        fc_item.call_id = "call-1"
        fc_item.name = "search"

        completed_resp = MagicMock()
        completed_resp.id = "resp-3"
        completed_resp.output = []
        completed_resp.usage = MagicMock()
        completed_resp.usage.input_tokens = 10
        completed_resp.usage.output_tokens = 5
        completed_resp.usage.input_tokens_details = None

        events = [
            _make_event(
                "response.output_item.added",
                item=fc_item,
                response=MagicMock(id="resp-3"),
            ),
            _make_event(
                "response.function_call_arguments.delta",
                item_id="fc_1",
                delta='{"q":',
            ),
            _make_event(
                "response.function_call_arguments.delta",
                item_id="fc_1",
                delta='"test"}',
            ),
            _make_event("response.completed", response=completed_resp),
        ]
        mock_create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )
        mock_client_cls.return_value.responses.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [
                        ToolCallBlock(
                            id="fc_1",
                            call_id="call-1",
                            name="search",
                            input='{"q":',
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ToolCallBlock(
                            id="fc_1",
                            call_id="call-1",
                            name="search",
                            input='"test"}',
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ToolCallBlock(
                            id="fc_1",
                            call_id="call-1",
                            name="search",
                            input='{"q":"test"}',
                        ),
                    ],
                ),
            ],
        )


# ---------------------------------------------------------------------------
# _format_tools tests
# ---------------------------------------------------------------------------

_FT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the weather",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the time",
            "parameters": {
                "type": "object",
                "properties": {"timezone": {"type": "string"}},
                "required": ["timezone"],
            },
        },
    },
]

_FT_TOOLS_RESPONSE = [
    {
        "type": "function",
        "name": "get_weather",
        "description": "Get the weather",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    {
        "type": "function",
        "name": "get_time",
        "description": "Get the time",
        "parameters": {
            "type": "object",
            "properties": {"timezone": {"type": "string"}},
            "required": ["timezone"],
        },
    },
]


class TestOpenAIResponseFormatTools(unittest.TestCase):
    """Tests for OpenAIResponseModel._format_tools."""

    def setUp(self) -> None:
        self.model = _make_model()

    def test_auto_mode(self) -> None:
        """Auto mode converts tools and sets choice to 'auto'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(fmt_choice, "auto")

    def test_none_mode(self) -> None:
        """None mode converts tools and sets choice to 'none'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="none"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(fmt_choice, "none")

    def test_required_mode(self) -> None:
        """Required mode converts tools and sets choice to 'required'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="required"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(fmt_choice, "required")

    def test_str_mode_force_call(self) -> None:
        """String mode forces a function call for the named tool."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(
            fmt_choice,
            {"type": "function", "name": "get_weather"},
        )

    def test_tools_filtered(self) -> None:
        """ToolChoice with tools list keeps the full tools schema and
        narrows the callable subset via ``allowed_tools`` to preserve
        prompt cache hits."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto", tools=["get_weather"]),
        )
        self.assertListEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertEqual(
            fmt_choice,
            {
                "type": "allowed_tools",
                "mode": "auto",
                "tools": [{"type": "function", "name": "get_weather"}],
            },
        )

    def test_no_tool_choice(self) -> None:
        """Tools are converted when tool_choice is None."""
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertEqual(fmt_tools, _FT_TOOLS_RESPONSE)
        self.assertIsNone(fmt_choice)
