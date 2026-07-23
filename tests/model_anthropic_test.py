# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for AnthropicChatModel with mocked API responses.

Tests cover both non-streaming and streaming modes.
Anthropic uses event-based streaming (message_start, content_block_start,
content_block_delta, message_delta events).
"""
import json
from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from utils import AnyString

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import AnthropicChatModel
from agentscope.credential import AnthropicCredential
from agentscope.tool import ToolChoice

A = AnyString()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return AnthropicChatModel(
        credential=AnthropicCredential(api_key="test"),
        model="claude-opus-4-5",
        stream=stream,
        context_size=200_000,
    )


def _mock_completion(
    text: Any = None,
    tool_calls: Any = None,
    thinking: Any = None,
    response_id: str = "msg-1",
) -> MagicMock:
    """Build a mock non-streaming Anthropic Message response."""
    blocks = []
    if thinking:
        b = MagicMock()
        b.type = "thinking"
        b.thinking = thinking
        b.signature = "sig123"
        blocks.append(b)
    if text:
        b = MagicMock()
        b.type = "text"
        b.text = text
        blocks.append(b)
    if tool_calls:
        for tc in tool_calls:
            b = MagicMock()
            b.type = "tool_use"
            b.id = tc["id"]
            b.name = tc["name"]
            b.input = tc["input"]
            blocks.append(b)

    resp = MagicMock()
    resp.id = response_id
    resp.content = blocks
    resp.usage = MagicMock()
    resp.usage.input_tokens = 10
    resp.usage.output_tokens = 5
    resp.usage.cache_creation_input_tokens = 0
    resp.usage.cache_read_input_tokens = 0
    return resp


def _make_event(event_type: str, **kwargs: Any) -> MagicMock:
    """Build a mock Anthropic streaming event."""
    event = MagicMock()
    event.type = event_type
    for key, val in kwargs.items():
        setattr(event, key, val)
    return event


class _MockAsyncEventStream:
    """Mock async iterator over Anthropic events."""

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


class TestAnthropicNonStream(IsolatedAsyncioTestCase):
    """Tests for AnthropicChatModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)

    @patch("anthropic.AsyncAnthropic")
    async def test_text_response(self, mock_client_cls: MagicMock) -> None:
        """Non-stream text response returns a single ChatResponse."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello!"),
        )
        mock_client_cls.return_value.messages.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (True, [TextBlock.model_construct(id=A, text="Hello!")]),
        )
        self.assertEqual(result.id, "msg-1")

    @patch("anthropic.AsyncAnthropic")
    async def test_tool_call_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Non-stream tool call response creates ToolCallBlocks."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                tool_calls=[
                    {
                        "id": "toolu_1",
                        "name": "get_weather",
                        "input": {"city": "Beijing"},
                    },
                ],
            ),
        )
        mock_client_cls.return_value.messages.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ToolCallBlock(
                        id="toolu_1",
                        name="get_weather",
                        input=json.dumps({"city": "Beijing"}),
                    ),
                ],
            ),
        )

    @patch("anthropic.AsyncAnthropic")
    async def test_thinking_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Non-stream response with reasoning creates ThinkingBlock."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                thinking="Deep thought...",
                text="Answer",
            ),
        )
        mock_client_cls.return_value.messages.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        thinking="Deep thought...",
                        signature="sig123",
                    ),
                    TextBlock.model_construct(id=A, text="Answer"),
                ],
            ),
        )

    @patch("anthropic.AsyncAnthropic")
    async def test_redacted_thinking_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Non-stream redacted_thinking block is preserved."""
        redacted = MagicMock()
        redacted.type = "redacted_thinking"
        redacted.data = "encrypted_data_abc"

        thinking = MagicMock()
        thinking.type = "thinking"
        thinking.thinking = "visible thought"
        thinking.signature = "sig_visible"

        text = MagicMock()
        text.type = "text"
        text.text = "Answer"

        resp = MagicMock()
        resp.id = "msg-redacted"
        resp.content = [thinking, redacted, text]
        resp.usage = MagicMock()
        resp.usage.input_tokens = 10
        resp.usage.output_tokens = 5
        resp.usage.cache_creation_input_tokens = 0
        resp.usage.cache_read_input_tokens = 0

        mock_create = AsyncMock(return_value=resp)
        mock_client_cls.return_value.messages.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        thinking="visible thought",
                        signature="sig_visible",
                    ),
                    ThinkingBlock.model_construct(
                        id=A,
                        thinking="",
                        redacted_thinking_data="encrypted_data_abc",
                    ),
                    TextBlock.model_construct(
                        id=A,
                        text="Answer",
                    ),
                ],
            ),
        )


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestAnthropicStream(IsolatedAsyncioTestCase):
    """Tests for AnthropicChatModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)

    @patch("anthropic.AsyncAnthropic")
    async def test_stream_text(self, mock_client_cls: MagicMock) -> None:
        """Stream text yields n deltas + 1 final with full content."""
        msg_usage = MagicMock()
        msg_usage.input_tokens = 10
        msg_usage.output_tokens = 0
        msg_usage.cache_creation_input_tokens = 0
        msg_usage.cache_read_input_tokens = 0

        message = MagicMock()
        message.id = "msg-1"
        message.usage = msg_usage

        delta1 = MagicMock()
        delta1.type = "text_delta"
        delta1.text = "Hello"

        delta2 = MagicMock()
        delta2.type = "text_delta"
        delta2.text = " world"

        msg_delta_usage = MagicMock()
        msg_delta_usage.output_tokens = 5

        text_start = MagicMock()
        text_start.type = "text"

        events = [
            _make_event("message_start", message=message),
            _make_event(
                "content_block_start",
                index=0,
                content_block=text_start,
            ),
            _make_event("content_block_delta", index=0, delta=delta1),
            _make_event("content_block_delta", index=0, delta=delta2),
            _make_event(
                "message_delta",
                usage=msg_delta_usage,
            ),
        ]
        mock_create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )
        mock_client_cls.return_value.messages.create = mock_create

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
        self.assertEqual(responses[-1].id, "msg-1")

    @patch("anthropic.AsyncAnthropic")
    async def test_stream_thinking_and_text(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream thinking + text yields deltas then final with signature."""
        msg_usage = MagicMock()
        msg_usage.input_tokens = 10
        msg_usage.output_tokens = 0
        msg_usage.cache_creation_input_tokens = 0
        msg_usage.cache_read_input_tokens = 0

        message = MagicMock()
        message.id = "msg-2"
        message.usage = msg_usage

        thinking_delta = MagicMock()
        thinking_delta.type = "thinking_delta"
        thinking_delta.thinking = "Let me think"

        sig_delta = MagicMock()
        sig_delta.type = "signature_delta"
        sig_delta.signature = "sig_abc"

        text_delta = MagicMock()
        text_delta.type = "text_delta"
        text_delta.text = "Result"

        thinking_start = MagicMock()
        thinking_start.type = "thinking"

        text_start = MagicMock()
        text_start.type = "text"

        events = [
            _make_event("message_start", message=message),
            _make_event(
                "content_block_start",
                index=0,
                content_block=thinking_start,
            ),
            _make_event(
                "content_block_delta",
                index=0,
                delta=thinking_delta,
            ),
            _make_event("content_block_delta", index=0, delta=sig_delta),
            _make_event(
                "content_block_start",
                index=1,
                content_block=text_start,
            ),
            _make_event("content_block_delta", index=1, delta=text_delta),
        ]
        mock_create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )
        mock_client_cls.return_value.messages.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            thinking="Let me think",
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            thinking="",
                            signature="sig_abc",
                        ),
                    ],
                ),
                (False, [TextBlock.model_construct(id=A, text="Result")]),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            thinking="Let me think",
                            signature="sig_abc",
                        ),
                        TextBlock.model_construct(id=A, text="Result"),
                    ],
                ),
            ],
        )

    @patch("anthropic.AsyncAnthropic")
    async def test_stream_redacted_thinking(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream redacted_thinking block is emitted at
        content_block_start."""
        msg_usage = MagicMock()
        msg_usage.input_tokens = 10
        msg_usage.output_tokens = 0
        msg_usage.cache_creation_input_tokens = 0
        msg_usage.cache_read_input_tokens = 0

        message = MagicMock()
        message.id = "msg-r"
        message.usage = msg_usage

        redacted_block = MagicMock()
        redacted_block.type = "redacted_thinking"
        redacted_block.data = "encrypted_stream_data"

        text_delta = MagicMock()
        text_delta.type = "text_delta"
        text_delta.text = "Result"

        text_start = MagicMock()
        text_start.type = "text"

        events = [
            _make_event("message_start", message=message),
            _make_event(
                "content_block_start",
                index=0,
                content_block=redacted_block,
            ),
            _make_event(
                "content_block_start",
                index=1,
                content_block=text_start,
            ),
            _make_event(
                "content_block_delta",
                index=1,
                delta=text_delta,
            ),
        ]
        mock_create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )
        mock_client_cls.return_value.messages.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            thinking="",
                            redacted_thinking_data="encrypted_stream_data",
                        ),
                    ],
                ),
                (
                    False,
                    [
                        TextBlock.model_construct(
                            id=A,
                            text="Result",
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            thinking="",
                            redacted_thinking_data="encrypted_stream_data",
                        ),
                        TextBlock.model_construct(
                            id=A,
                            text="Result",
                        ),
                    ],
                ),
            ],
        )

    @patch("anthropic.AsyncAnthropic")
    async def test_stream_tool_call(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream tool call yields partial deltas then full accumulated
        input."""
        msg_usage = MagicMock()
        msg_usage.input_tokens = 10
        msg_usage.output_tokens = 0
        msg_usage.cache_creation_input_tokens = 0
        msg_usage.cache_read_input_tokens = 0

        message = MagicMock()
        message.id = "msg-3"
        message.usage = msg_usage

        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "toolu_1"
        tool_block.name = "get_weather"

        json_delta1 = MagicMock()
        json_delta1.type = "input_json_delta"
        json_delta1.partial_json = '{"city":'

        json_delta2 = MagicMock()
        json_delta2.type = "input_json_delta"
        json_delta2.partial_json = '"BJ"}'

        events = [
            _make_event("message_start", message=message),
            _make_event(
                "content_block_start",
                index=0,
                content_block=tool_block,
            ),
            _make_event("content_block_delta", index=0, delta=json_delta1),
            _make_event("content_block_delta", index=0, delta=json_delta2),
        ]
        mock_create = AsyncMock(
            return_value=_MockAsyncEventStream(events),
        )
        mock_client_cls.return_value.messages.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                # Anthropic emits a ``content_block_start`` event before the
                # first delta, which surfaces as an initial empty-input
                # ``ToolCallBlock`` delta.
                (
                    False,
                    [
                        ToolCallBlock(
                            id="toolu_1",
                            name="get_weather",
                            input="",
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ToolCallBlock(
                            id="toolu_1",
                            name="get_weather",
                            input='{"city":',
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ToolCallBlock(
                            id="toolu_1",
                            name="get_weather",
                            input='"BJ"}',
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ToolCallBlock(
                            id="toolu_1",
                            name="get_weather",
                            input='{"city":"BJ"}',
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

_FT_TOOLS_ANTHROPIC = [
    {
        "name": "get_weather",
        "description": "Get the weather",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
    {
        "name": "get_time",
        "description": "Get the time",
        "input_schema": {
            "type": "object",
            "properties": {"timezone": {"type": "string"}},
            "required": ["timezone"],
        },
    },
]


class TestAnthropicFormatTools(unittest.TestCase):
    """Tests for AnthropicChatModel._format_tools."""

    def setUp(self) -> None:
        """Set up model instance."""
        self.model = _make_model()

    def test_auto_mode(self) -> None:
        """Auto mode returns converted tools and type=auto."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_ANTHROPIC)
        self.assertEqual(fmt_choice, {"type": "auto"})

    def test_none_mode(self) -> None:
        """None mode returns converted tools and type=none."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="none"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_ANTHROPIC)
        self.assertEqual(fmt_choice, {"type": "none"})

    def test_required_mode(self) -> None:
        """Required mode maps to type=any."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="required"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_ANTHROPIC)
        self.assertEqual(fmt_choice, {"type": "any"})

    def test_str_mode_force_call(self) -> None:
        """A specific tool name forces that tool call."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_ANTHROPIC)
        self.assertEqual(fmt_choice, {"type": "tool", "name": "get_weather"})

    def test_tools_filtered(self) -> None:
        """When tool_choice.tools is set, only those tools are included."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto", tools=["get_weather"]),
        )
        self.assertEqual(len(fmt_tools), 1)
        self.assertEqual(fmt_tools[0]["name"], "get_weather")
        self.assertEqual(fmt_choice, {"type": "auto"})

    def test_no_tool_choice(self) -> None:
        """Without tool_choice, returns converted tools and None."""
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertEqual(fmt_tools, _FT_TOOLS_ANTHROPIC)
        self.assertIsNone(fmt_choice)
