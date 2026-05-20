# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for GeminiChatModel with mocked API responses.

Tests cover both non-streaming and streaming modes.
Gemini uses google.genai client with async iterator streaming.
"""
import json
from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from utils import AnyString

from agentscope.message import TextBlock, ToolCallBlock, ThinkingBlock
from agentscope.model import GeminiChatModel
from agentscope.credential import GeminiCredential
from agentscope.tool import ToolChoice

A = AnyString()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return GeminiChatModel(
        credential=GeminiCredential(api_key="test"),
        model="gemini-2.5-flash",
        stream=stream,
        context_size=1_048_576,
    )


def _make_part(
    text: str | None = None,
    thought: bool = False,
    function_call: dict | None = None,
    thought_signature: Any = None,
) -> MagicMock:
    """Build a mock Gemini Part."""
    part = MagicMock()
    part.text = text
    part.thought = thought
    part.thought_signature = thought_signature
    if function_call:
        part.function_call = MagicMock()
        part.function_call.name = function_call["name"]
        part.function_call.args = function_call.get("args", {})
        part.function_call.id = function_call.get("id", "call-1")
    else:
        part.function_call = None
    return part


def _mock_completion(
    parts: list,
    response_id: str = "resp-gem-1",
) -> MagicMock:
    """Build a mock non-streaming Gemini response."""
    resp = MagicMock()
    resp.response_id = response_id
    resp.candidates = [MagicMock()]
    resp.candidates[0].content = MagicMock()
    resp.candidates[0].content.parts = parts
    resp.usage_metadata = MagicMock()
    resp.usage_metadata.prompt_token_count = 10
    resp.usage_metadata.candidates_token_count = 5
    return resp


def _make_stream_chunk(
    parts: list,
    response_id: str = "resp-gem-1",
) -> MagicMock:
    """Build a single mock streaming chunk."""
    chunk = MagicMock()
    chunk.response_id = response_id
    chunk.candidates = [MagicMock()]
    chunk.candidates[0].content = MagicMock()
    chunk.candidates[0].content.parts = parts
    chunk.usage_metadata = MagicMock()
    chunk.usage_metadata.prompt_token_count = 10
    chunk.usage_metadata.candidates_token_count = 5
    return chunk


class _MockAsyncStream:
    """Mock async iterator for Gemini stream."""

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


class TestGeminiNonStream(IsolatedAsyncioTestCase):
    """Tests for GeminiChatModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)

    @patch("google.genai.Client")
    async def test_text_response(self, mock_client_cls: MagicMock) -> None:
        """Non-stream text response returns a single ChatResponse."""
        parts = [_make_part(text="Hello!")]
        mock_client_cls.return_value.aio.models.generate_content = AsyncMock(
            return_value=_mock_completion(parts),
        )

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (True, [TextBlock.model_construct(id=A, text="Hello!")]),
        )

    @patch("google.genai.Client")
    async def test_tool_call_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Non-stream tool call response creates ToolCallBlocks."""
        parts = [
            _make_part(
                function_call={
                    "name": "get_weather",
                    "args": {"city": "Tokyo"},
                    "id": "call-1",
                },
            ),
        ]
        mock_client_cls.return_value.aio.models.generate_content = AsyncMock(
            return_value=_mock_completion(parts),
        )

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ToolCallBlock(
                        id="call-1",
                        name="get_weather",
                        input=json.dumps(
                            {"city": "Tokyo"},
                            ensure_ascii=False,
                        ),
                    ),
                ],
            ),
        )

    @patch("google.genai.Client")
    async def test_thinking_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Non-stream response with reasoning creates ThinkingBlock."""
        parts = [
            _make_part(text="Let me think...", thought=True),
            _make_part(text="Answer"),
        ]
        mock_client_cls.return_value.aio.models.generate_content = AsyncMock(
            return_value=_mock_completion(parts),
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


class TestGeminiStream(IsolatedAsyncioTestCase):
    """Tests for GeminiChatModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)

    @patch("google.genai.Client")
    async def test_stream_text(self, mock_client_cls: MagicMock) -> None:
        """Stream text yields n deltas + 1 final with full content."""
        chunks = [
            _make_stream_chunk([_make_part(text="Hello")]),
            _make_stream_chunk([_make_part(text=" world")]),
        ]
        mock_client_cls.return_value.aio.models.generate_content_stream = (
            AsyncMock(return_value=_MockAsyncStream(chunks))
        )

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

    @patch("google.genai.Client")
    async def test_stream_thinking_and_text(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream thinking + text yields deltas then accumulated final."""
        chunks = [
            _make_stream_chunk([_make_part(text="Think", thought=True)]),
            _make_stream_chunk([_make_part(text="Answer")]),
        ]
        mock_client_cls.return_value.aio.models.generate_content_stream = (
            AsyncMock(return_value=_MockAsyncStream(chunks))
        )

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [ThinkingBlock.model_construct(id=A, thinking="Think")],
                ),
                (False, [TextBlock.model_construct(id=A, text="Answer")]),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(id=A, thinking="Think"),
                        TextBlock.model_construct(id=A, text="Answer"),
                    ],
                ),
            ],
        )

    @patch("google.genai.Client")
    async def test_stream_tool_call(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream tool call yields delta then final with same ToolCallBlock."""
        chunks = [
            _make_stream_chunk(
                [
                    _make_part(
                        function_call={
                            "name": "search",
                            "args": {"q": "test"},
                            "id": "call-1",
                        },
                    ),
                ],
            ),
        ]
        mock_client_cls.return_value.aio.models.generate_content_stream = (
            AsyncMock(return_value=_MockAsyncStream(chunks))
        )

        gen = await self.model([])
        responses = [r async for r in gen]

        tool_block = ToolCallBlock(
            id="call-1",
            name="search",
            input=json.dumps({"q": "test"}, ensure_ascii=False),
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

_FT_TOOLS_GEMINI = [
    {
        "function_declarations": [
            {
                "name": "get_weather",
                "description": "Get the weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
            {
                "name": "get_time",
                "description": "Get the time",
                "parameters": {
                    "type": "object",
                    "properties": {"timezone": {"type": "string"}},
                    "required": ["timezone"],
                },
            },
        ],
    },
]


class TestGeminiFormatTools(unittest.TestCase):
    """Tests for GeminiChatModel._format_tools."""

    def setUp(self) -> None:
        """Set up model instance."""
        self.model = _make_model()

    def test_auto_mode(self) -> None:
        """Auto mode returns function_declarations and AUTO config."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_GEMINI)
        self.assertEqual(
            fmt_choice,
            {"function_calling_config": {"mode": "AUTO"}},
        )

    def test_none_mode(self) -> None:
        """None mode returns function_declarations and NONE config."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="none"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_GEMINI)
        self.assertEqual(
            fmt_choice,
            {"function_calling_config": {"mode": "NONE"}},
        )

    def test_required_mode(self) -> None:
        """Required mode maps to ANY config."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="required"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_GEMINI)
        self.assertEqual(
            fmt_choice,
            {"function_calling_config": {"mode": "ANY"}},
        )

    def test_str_mode_force_call(self) -> None:
        """A specific tool name restricts via allowed_function_names."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS_GEMINI)
        self.assertEqual(
            fmt_choice,
            {
                "function_calling_config": {
                    "mode": "ANY",
                    "allowed_function_names": ["get_weather"],
                },
            },
        )

    def test_tools_filtered(self) -> None:
        """When tool_choice.tools is set, only those tools are included."""
        fmt_tools, _ = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto", tools=["get_weather"]),
        )
        self.assertEqual(len(fmt_tools[0]["function_declarations"]), 1)
        self.assertEqual(
            fmt_tools[0]["function_declarations"][0]["name"],
            "get_weather",
        )

    def test_no_tool_choice(self) -> None:
        """Without tool_choice, returns function_declarations and None."""
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertEqual(fmt_tools, _FT_TOOLS_GEMINI)
        self.assertIsNone(fmt_choice)
