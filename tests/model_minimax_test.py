# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for MiniMaxChatModel with mocked API responses.

MiniMax's M-series chat models run through MiniMax's officially
recommended Anthropic-compatible API, so the tests follow the same
shape as :mod:`tests.model_anthropic_test`:

- Non-stream mode returns a single ChatResponse with is_last=True.
- Stream mode yields delta ChatResponses (is_last=False) followed by a
  final ChatResponse (is_last=True) with the full accumulated content.
- ``_format_tools`` converts OpenAI-style schemas into Anthropic's flat
  format and maps modes to Anthropic's type-based tool_choice.
- Model card listing returns the expected MiniMax models.
"""

import json
from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from utils import AnyString

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import MiniMaxChatModel
from agentscope.credential import MiniMaxCredential
from agentscope.tool import ToolChoice

A = AnyString()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return MiniMaxChatModel(
        credential=MiniMaxCredential(api_key="test"),
        model="MiniMax-M3",
        stream=stream,
        context_size=512_000,
    )


def _mock_completion(
    text: Any = None,
    tool_calls: Any = None,
    thinking: Any = None,
    response_id: str = "msg-1",
) -> MagicMock:
    """Build a mock non-streaming Anthropic-compatible Message response."""
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
    """Build a mock Anthropic-compatible streaming event."""
    event = MagicMock()
    event.type = event_type
    for key, val in kwargs.items():
        setattr(event, key, val)
    return event


class _MockAsyncEventStream:
    """Mock async iterator over Anthropic-compatible events."""

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


class TestMiniMaxNonStream(IsolatedAsyncioTestCase):
    """Tests for MiniMaxChatModel in non-streaming mode."""

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
                        "input": {"city": "Shanghai"},
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
                        input=json.dumps({"city": "Shanghai"}),
                    ),
                ],
            ),
        )

    @patch("anthropic.AsyncAnthropic")
    async def test_thinking_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Non-stream response with thinking creates ThinkingBlock."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                thinking="Step by step...",
                text="42",
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
                        thinking="Step by step...",
                        signature="sig123",
                    ),
                    TextBlock.model_construct(id=A, text="42"),
                ],
            ),
        )


class TestMiniMaxModelParameters(unittest.TestCase):
    """Tests for MiniMaxChatModel.Parameters."""

    def test_default_thinking_disabled(self) -> None:
        """Extended thinking is off by default to match other chat models."""
        params = MiniMaxChatModel.Parameters()
        self.assertIs(params.thinking_enable, False)
        self.assertIsNone(params.thinking_budget)

    def test_thinking_budget_must_be_positive(self) -> None:
        """thinking_budget must be > 0 when provided."""
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            MiniMaxChatModel.Parameters(thinking_budget=0)

    def test_default_base_url_is_anthropic_compatible(self) -> None:
        """Default base URL points to MiniMax's Anthropic-compatible
        endpoint."""
        cred = MiniMaxCredential(api_key="test")
        self.assertEqual(cred.base_url, "https://api.minimax.io/anthropic")


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestMiniMaxStream(IsolatedAsyncioTestCase):
    """Tests for MiniMaxChatModel in streaming mode."""

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
        delta1.text = "Hi"

        delta2 = MagicMock()
        delta2.type = "text_delta"
        delta2.text = " there"

        msg_delta_usage = MagicMock()
        msg_delta_usage.output_tokens = 2

        events = [
            _make_event("message_start", message=message),
            _make_event("content_block_delta", index=0, delta=delta1),
            _make_event("content_block_delta", index=0, delta=delta2),
            _make_event("message_delta", usage=msg_delta_usage),
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
                (False, [TextBlock.model_construct(id=A, text="Hi")]),
                (False, [TextBlock.model_construct(id=A, text=" there")]),
                (True, [TextBlock.model_construct(id=A, text="Hi there")]),
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

        events = [
            _make_event("message_start", message=message),
            _make_event("content_block_delta", index=0, delta=thinking_delta),
            _make_event("content_block_delta", index=0, delta=sig_delta),
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
        tool_block.name = "search"

        json_delta1 = MagicMock()
        json_delta1.type = "input_json_delta"
        json_delta1.partial_json = '{"q":'

        json_delta2 = MagicMock()
        json_delta2.type = "input_json_delta"
        json_delta2.partial_json = '"test"}'

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
                (
                    False,
                    [
                        ToolCallBlock(
                            id="toolu_1",
                            name="search",
                            input='{"q":',
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ToolCallBlock(
                            id="toolu_1",
                            name="search",
                            input='"test"}',
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ToolCallBlock(
                            id="toolu_1",
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


class TestMiniMaxFormatTools(unittest.TestCase):
    """Tests for MiniMaxChatModel._format_tools."""

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
        """A specific tool name returns a type=tool dict."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_ANTHROPIC)
        self.assertEqual(
            fmt_choice,
            {"type": "tool", "name": "get_weather"},
        )

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


# ---------------------------------------------------------------------------
# Model card listing tests
# ---------------------------------------------------------------------------


class TestMiniMaxModelListing(unittest.TestCase):
    """Tests for MiniMax model card discovery."""

    def test_list_models_returns_minimax_cards(self) -> None:
        """list_models() returns the three MiniMax model cards."""
        cards = MiniMaxChatModel.list_models()
        names = {c.name for c in cards}
        self.assertIn("MiniMax-M3", names)
        self.assertIn("MiniMax-M2.7", names)
        self.assertIn("MiniMax-M2.7-highspeed", names)

    def test_m3_is_active(self) -> None:
        """The M3 model card is marked active."""
        cards = MiniMaxChatModel.list_models()
        m3 = next(c for c in cards if c.name == "MiniMax-M3")
        self.assertEqual(m3.status, "active")

    def test_m3_supports_image_input(self) -> None:
        """The M3 model card advertises image/* input support."""
        cards = MiniMaxChatModel.list_models()
        m3 = next(c for c in cards if c.name == "MiniMax-M3")
        image_inputs = [t for t in m3.input_types if t.startswith("image/")]
        self.assertGreater(len(image_inputs), 0)

    def test_credential_lists_models(self) -> None:
        """MiniMaxCredential.list_models delegates to the chat model class."""
        cards = MiniMaxCredential.list_models()
        names = {c.name for c in cards}
        self.assertIn("MiniMax-M3", names)


if __name__ == "__main__":
    unittest.main()
