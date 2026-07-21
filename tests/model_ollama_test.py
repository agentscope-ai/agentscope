# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OllamaChatModel with mocked API responses.

Tests cover both non-streaming and streaming modes.
Ollama uses ollama.AsyncClient with async iterator streaming.
"""
import json
from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from utils import AnyString

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import OllamaChatModel
from agentscope.tool import ToolChoice

A = AnyString()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return OllamaChatModel(
        model="qwen3:8b",
        stream=stream,
        context_size=40_960,
    )


def _mock_completion(
    content: str = "",
    thinking: str | None = None,
    tool_calls: list | None = None,
) -> MagicMock:
    """Build a mock non-streaming Ollama response."""
    msg = MagicMock()
    msg.content = content
    msg.thinking = thinking

    if tool_calls:
        tc_mocks = []
        for tc in tool_calls:
            m = MagicMock()
            m.function.name = tc["name"]
            m.function.arguments = tc["args"]
            tc_mocks.append(m)
        msg.tool_calls = tc_mocks
    else:
        msg.tool_calls = None

    resp = MagicMock()
    resp.message = msg
    resp.prompt_eval_count = 10
    resp.eval_count = 5
    resp.id = None
    return resp


def _make_stream_chunk(
    content: str = "",
    thinking: str | None = None,
    tool_calls: list | None = None,
) -> MagicMock:
    """Build a single mock Ollama streaming chunk."""
    msg = MagicMock()
    msg.content = content
    msg.thinking = thinking

    if tool_calls:
        tc_mocks = []
        for tc in tool_calls:
            m = MagicMock()
            m.function.name = tc["name"]
            m.function.arguments = tc["args"]
            tc_mocks.append(m)
        msg.tool_calls = tc_mocks
    else:
        msg.tool_calls = None

    chunk = MagicMock()
    chunk.message = msg
    chunk.prompt_eval_count = 10
    chunk.eval_count = 5
    chunk.id = None
    return chunk


class _MockAsyncStream:
    """Mock async iterator for Ollama stream."""

    def __init__(self, chunks: list) -> None:
        self._chunks = chunks
        self._index = 0

    def __aiter__(self) -> "_MockAsyncStream":
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


# ---------------------------------------------------------------------------
# Non-streaming tests
# ---------------------------------------------------------------------------


class TestOllamaNonStream(IsolatedAsyncioTestCase):
    """Tests for OllamaChatModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)
        # Client is built eagerly in __init__; inject a mock onto the
        # instance so chat() hits it instead of the network.
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_text_response(self) -> None:
        """Non-stream text response returns a single ChatResponse."""
        self.mock_client.chat = AsyncMock(
            return_value=_mock_completion(content="Hello!"),
        )

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (True, [TextBlock.model_construct(id=A, text="Hello!")]),
        )

    async def test_tool_call_response(self) -> None:
        """Parsing a tool-call response creates a ToolCallBlock."""
        self.mock_client.chat = AsyncMock(
            return_value=_mock_completion(
                tool_calls=[
                    {"name": "get_weather", "args": {"city": "SH"}},
                ],
            ),
        )

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ToolCallBlock(
                        id="0_get_weather",
                        name="get_weather",
                        input=json.dumps({"city": "SH"}),
                    ),
                ],
            ),
        )

    async def test_thinking_response(self) -> None:
        """Non-stream thinking plus text returns ThinkingBlock then
        TextBlock."""
        self.mock_client.chat = AsyncMock(
            return_value=_mock_completion(
                content="Answer",
                thinking="Let me think...",
            ),
        )

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        thinking="Let me think...",
                    ),
                    TextBlock.model_construct(id=A, text="Answer"),
                ],
            ),
        )


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestOllamaStream(IsolatedAsyncioTestCase):
    """Tests for OllamaChatModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)
        # Client is built eagerly in __init__; inject a mock onto the
        # instance so chat() hits it instead of the network.
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_stream_text(self) -> None:
        """Stream text yields deltas then full content."""
        chunks = [
            _make_stream_chunk(content="Hi"),
            _make_stream_chunk(content=" there"),
        ]
        self.mock_client.chat = AsyncMock(
            return_value=_MockAsyncStream(chunks),
        )

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

    async def test_stream_thinking_and_text(self) -> None:
        """Stream thinking and text deltas then final with accumulated
        content."""
        chunks = [
            _make_stream_chunk(thinking="Think step"),
            _make_stream_chunk(content="Result"),
        ]
        self.mock_client.chat = AsyncMock(
            return_value=_MockAsyncStream(chunks),
        )

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
                            thinking="Think step",
                        ),
                    ],
                ),
                (False, [TextBlock.model_construct(id=A, text="Result")]),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            thinking="Think step",
                        ),
                        TextBlock.model_construct(id=A, text="Result"),
                    ],
                ),
            ],
        )

    async def test_stream_tool_call(self) -> None:
        """Stream tool-call chunk yields delta then final with same
        ToolCallBlock."""
        chunks = [
            _make_stream_chunk(
                tool_calls=[
                    {"name": "search", "args": {"q": "hello"}},
                ],
            ),
        ]
        self.mock_client.chat = AsyncMock(
            return_value=_MockAsyncStream(chunks),
        )

        gen = await self.model([])
        responses = [r async for r in gen]

        tool_block = ToolCallBlock(
            id="0_search",
            name="search",
            input=json.dumps({"q": "hello"}),
        )
        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (False, [tool_block]),
                (True, [tool_block]),
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


class TestOllamaFormatTools(unittest.TestCase):
    """Tests for OllamaChatModel._format_tools."""

    def setUp(self) -> None:
        self.model = _make_model()

    def test_tools_forwarded_no_choice(self) -> None:
        """Tools are forwarded unchanged when tool_choice is None."""
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertIsNone(fmt_choice)

    def test_tools_filtered(self) -> None:
        """ToolChoice with tools list filters to matching function names."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto", tools=["get_weather"]),
        )
        self.assertIsNotNone(fmt_tools)
        assert fmt_tools is not None
        self.assertEqual(len(fmt_tools), 1)
        self.assertEqual(fmt_tools[0]["function"]["name"], "get_weather")
        self.assertIsNone(fmt_choice)
