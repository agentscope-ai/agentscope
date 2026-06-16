# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for DashScopeChatModel with mocked API responses.

Tests cover both non-streaming and streaming modes.
"""
import base64
import io
import wave
from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from utils import AnyString

from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ThinkingBlock,
    DataBlock,
)
from agentscope.model import DashScopeChatModel
from agentscope.credential import DashScopeCredential
from agentscope.tool import ToolChoice

A = AnyString()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return DashScopeChatModel(
        credential=DashScopeCredential(api_key="test"),
        model="qwen3-max",
        stream=stream,
        max_retries=3,
        context_size=131_072,
        parameters=DashScopeChatModel.Parameters(
            max_tokens=1000,
            thinking_enable=True,
            thinking_budget=100,
        ),
    )


def _mock_completion(
    text: Any = None,
    tool_calls: Any = None,
    reasoning: Any = None,
    response_id: str = "req-1",
) -> MagicMock:
    """Build a mock non-streaming ChatCompletion response."""
    msg = MagicMock()
    msg.content = text
    msg.reasoning_content = reasoning
    msg.tool_calls = None

    if tool_calls:
        tc_mocks = []
        for tc in tool_calls:
            m = MagicMock()
            m.id = tc["id"]
            m.function.name = tc["name"]
            m.function.arguments = tc["arguments"]
            tc_mocks.append(m)
        msg.tool_calls = tc_mocks

    choice = MagicMock()
    choice.message = msg

    resp = MagicMock()
    resp.id = response_id
    resp.choices = [choice]
    resp.usage = MagicMock()
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    resp.usage.prompt_tokens_details = None
    return resp


def _make_stream_chunk(
    delta_text: str | None = None,
    delta_reasoning: str | None = None,
    tool_calls: list | None = None,
    delta_audio: dict | None = None,
    response_id: str = "req-1",
    usage: dict | None = None,
    has_choices: bool = True,
) -> MagicMock:
    """Build a single mock streaming chunk."""
    chunk = MagicMock()
    chunk.id = response_id

    if usage:
        chunk.usage = MagicMock()
        chunk.usage.prompt_tokens = usage.get("prompt_tokens", 0)
        chunk.usage.completion_tokens = usage.get("completion_tokens", 0)
        chunk.usage.prompt_tokens_details = None
    else:
        chunk.usage = None

    if has_choices:
        delta = MagicMock()
        delta.content = delta_text
        delta.reasoning_content = delta_reasoning
        delta.tool_calls = tool_calls
        delta.audio = delta_audio
        choice = MagicMock()
        choice.delta = delta
        chunk.choices = [choice]
    else:
        chunk.choices = []

    return chunk


def _make_tool_call_delta(
    index: int,
    tc_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> MagicMock:
    tc = MagicMock()
    tc.index = index
    tc.id = tc_id
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


class _MockAsyncStream:
    """Mock async stream (context manager + async iterator)."""

    def __init__(self, chunks: list) -> None:
        self._chunks = chunks
        self._index = 0

    async def __aenter__(self) -> "_MockAsyncStream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

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


class TestDashScopeNonStream(IsolatedAsyncioTestCase):
    """Tests for DashScopeChatModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)

    @patch("openai.AsyncClient")
    async def test_text_response(self, mock_client_cls: MagicMock) -> None:
        """Non-stream text response returns a single ChatResponse."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello!"),
        )
        mock_client_cls.return_value.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (True, [TextBlock.model_construct(id=A, text="Hello!")]),
        )
        self.assertEqual(result.id, "req-1")

    @patch("openai.AsyncClient")
    async def test_tool_call_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Non-stream tool call response creates ToolCallBlocks."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "get_weather",
                        "arguments": '{"city":"Hangzhou"}',
                    },
                ],
            ),
        )
        mock_client_cls.return_value.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ToolCallBlock(
                        id="call-1",
                        name="get_weather",
                        input='{"city":"Hangzhou"}',
                    ),
                ],
            ),
        )

    @patch("openai.AsyncClient")
    async def test_thinking_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Non-stream response with reasoning creates ThinkingBlock."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                text="42",
                reasoning="Reasoning step...",
            ),
        )
        mock_client_cls.return_value.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        thinking="Reasoning step...",
                    ),
                    TextBlock.model_construct(id=A, text="42"),
                ],
            ),
        )


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestDashScopeStream(IsolatedAsyncioTestCase):
    """Tests for DashScopeChatModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)

    @patch("openai.AsyncClient")
    async def test_stream_text(self, mock_client_cls: MagicMock) -> None:
        """Stream text yields n deltas + 1 final with full content."""
        chunks = [
            _make_stream_chunk(delta_text="Hello"),
            _make_stream_chunk(delta_text=" world"),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 10, "completion_tokens": 2},
            ),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        mock_client_cls.return_value.chat.completions.create = mock_create

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
    async def test_stream_thinking_and_text(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream reasoning + text yields deltas then accumulated final."""
        chunks = [
            _make_stream_chunk(delta_reasoning="Think"),
            _make_stream_chunk(delta_reasoning="ing"),
            _make_stream_chunk(delta_text="Answer"),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            ),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        mock_client_cls.return_value.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [ThinkingBlock.model_construct(id=A, thinking="Think")],
                ),
                (False, [ThinkingBlock.model_construct(id=A, thinking="ing")]),
                (False, [TextBlock.model_construct(id=A, text="Answer")]),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            thinking="Thinking",
                        ),
                        TextBlock.model_construct(id=A, text="Answer"),
                    ],
                ),
            ],
        )

    @patch("openai.AsyncClient")
    async def test_stream_tool_calls(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream tool call chunks accumulate into final ToolCallBlock."""
        chunks = [
            _make_stream_chunk(
                tool_calls=[
                    _make_tool_call_delta(0, "call-1", "search", '{"q":'),
                ],
            ),
            _make_stream_chunk(
                tool_calls=[
                    _make_tool_call_delta(0, None, None, '"hello"}'),
                ],
            ),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            ),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        mock_client_cls.return_value.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [
                        ToolCallBlock(
                            id="call-1",
                            name="search",
                            input='{"q":',
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ToolCallBlock(
                            id="call-1",
                            name="search",
                            input='"hello"}',
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ToolCallBlock(
                            id="call-1",
                            name="search",
                            input='{"q":"hello"}',
                        ),
                    ],
                ),
            ],
        )

    @patch("openai.AsyncClient")
    async def test_stream_usage(self, mock_client_cls: MagicMock) -> None:
        """Stream usage chunk attaches token counts to final response."""
        chunks = [
            _make_stream_chunk(delta_text="X"),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 50, "completion_tokens": 10},
            ),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        mock_client_cls.return_value.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (False, [TextBlock.model_construct(id=A, text="X")]),
                (True, [TextBlock.model_construct(id=A, text="X")]),
            ],
        )
        self.assertEqual(responses[-1].usage.input_tokens, 50)
        self.assertEqual(responses[-1].usage.output_tokens, 10)

    @patch("openai.AsyncClient")
    async def test_stream_audio_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream PCM deltas produce per-chunk DataBlocks (first chunk
        prefixed with a streaming WAV header) sharing a stable id, plus a
        final fixed-size WAV block readable by the ``wave`` module."""
        pcm1 = bytes([1, 2, 3, 4])
        pcm2 = bytes([5, 6, 7, 8])
        pcm3 = bytes([9, 10, 11, 12])
        pcm_full = pcm1 + pcm2 + pcm3

        chunks = [
            _make_stream_chunk(
                delta_audio={"data": base64.b64encode(pcm1).decode()},
            ),
            _make_stream_chunk(
                delta_audio={"data": base64.b64encode(pcm2).decode()},
            ),
            _make_stream_chunk(
                delta_audio={"data": base64.b64encode(pcm3).decode()},
            ),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 5, "completion_tokens": 3},
            ),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        mock_client_cls.return_value.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]
        self.assertEqual(len(responses), 4)

        # All four chunks (3 deltas + 1 final) must share the same audio
        # block id so downstream consumers stitch them as one stream.
        all_audio_ids = {
            block.id
            for r in responses
            for block in r.content
            if isinstance(block, DataBlock)
        }
        self.assertEqual(len(all_audio_ids), 1)

        # First delta: WAV header (44 bytes, "RIFF"..."WAVE") + pcm1.
        first_payload = base64.b64decode(responses[0].content[0].source.data)
        self.assertEqual(len(first_payload), 44 + len(pcm1))
        self.assertEqual(first_payload[:4], b"RIFF")
        self.assertEqual(first_payload[8:12], b"WAVE")
        self.assertEqual(first_payload[44:], pcm1)

        # Subsequent deltas: raw PCM only, no header.
        for resp, pcm in zip(responses[1:3], [pcm2, pcm3]):
            payload = base64.b64decode(resp.content[0].source.data)
            self.assertEqual(payload, pcm)

        # Final ``is_last`` block: a fixed-size WAV the ``wave`` module
        # can parse end-to-end at 24kHz / mono / 16-bit.
        final = responses[-1]
        self.assertTrue(final.is_last)
        final_audio = next(
            b for b in final.content if isinstance(b, DataBlock)
        )
        wav_bytes = base64.b64decode(final_audio.source.data)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.getframerate(), 24000)
            frames = wav.readframes(wav.getnframes())
        self.assertEqual(frames, pcm_full)


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


class TestDashScopeFormatTools(unittest.TestCase):
    """Tests for DashScopeChatModel._format_tools."""

    def setUp(self) -> None:
        """Set up model instance."""
        self.model = _make_model()

    def test_auto_mode(self) -> None:
        """Auto mode returns tools unchanged and string 'auto'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(fmt_choice, "auto")

    def test_none_mode(self) -> None:
        """None mode returns tools unchanged and string 'none'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="none"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(fmt_choice, "none")

    def test_required_mode_warns(self) -> None:
        """Required mode emits a DeprecationWarning and falls back to auto."""
        with self.assertWarns(DeprecationWarning):
            fmt_tools, fmt_choice = self.model._format_tools(
                _FT_TOOLS,
                ToolChoice(mode="required"),
            )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(fmt_choice, "auto")

    def test_str_mode_force_call(self) -> None:
        """A specific tool name forces that tool call."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="get_weather"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(
            fmt_choice,
            {"type": "function", "function": {"name": "get_weather"}},
        )

    def test_tools_filtered(self) -> None:
        """When tool_choice.tools is set, only those tools are included."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="auto", tools=["get_weather"]),
        )
        self.assertEqual(len(fmt_tools), 1)
        self.assertEqual(fmt_tools[0]["function"]["name"], "get_weather")
        self.assertEqual(fmt_choice, "auto")

    def test_no_tool_choice(self) -> None:
        """Without tool_choice, returns tools and None."""
        fmt_tools, fmt_choice = self.model._format_tools(_FT_TOOLS, None)
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertIsNone(fmt_choice)
