# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OpenAIChatModel with mocked API responses.

Tests cover both non-streaming and streaming modes, verifying that:
- Non-stream mode returns a single ChatResponse with is_last=True.
- Stream mode yields n delta ChatResponses (is_last=False) followed by
  1 final ChatResponse (is_last=True) with the full accumulated content.
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
    Base64Source,
)
from agentscope.model import OpenAIChatModel
from agentscope.credential import OpenAICredential
from agentscope.tool import ToolChoice

A = AnyString()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(stream: bool = False) -> Any:
    return OpenAIChatModel(
        credential=OpenAICredential(api_key="test"),
        model="gpt-4o",
        stream=stream,
        context_size=128_000,
    )


def _mock_completion(
    text: Any = None,
    tool_calls: Any = None,
    reasoning: Any = None,
    response_id: str = "resp-1",
    audio: dict | None = None,
) -> MagicMock:
    """Build a mock non-streaming ChatCompletion response."""
    msg = MagicMock()
    msg.content = text
    msg.reasoning_content = reasoning
    msg.reasoning = None
    msg.audio = audio
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
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    resp.usage.prompt_tokens_details = None
    return resp


def _make_stream_chunk(
    delta_text: str | None = None,
    delta_reasoning: str | None = None,
    tool_calls: list | None = None,
    response_id: str = "resp-1",
    usage: dict | None = None,
    has_choices: bool = True,
    delta_audio: dict | None = None,
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
        delta.reasoning = None
        delta.audio = delta_audio
        delta.tool_calls = tool_calls
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
    """Build a tool_call delta item for streaming."""
    tc = MagicMock()
    tc.index = index
    tc.id = tc_id
    tc.function.name = name
    tc.function.arguments = arguments
    return tc


class _MockAsyncStream:
    """Mock async stream that acts as an async context manager + iterator."""

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


class TestOpenAIChatNonStream(IsolatedAsyncioTestCase):
    """Tests for OpenAIChatModel in non-streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)

    @patch("openai.AsyncClient")
    async def test_text_response(self, mock_client_cls: MagicMock) -> None:
        """Non-stream text response returns a single ChatResponse."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello world!"),
        )
        mock_client_cls.return_value.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (True, [TextBlock.model_construct(id=A, text="Hello world!")]),
        )
        self.assertEqual(result.id, "resp-1")

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
                        "arguments": '{"city":"Beijing"}',
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
                        input='{"city":"Beijing"}',
                    ),
                ],
            ),
        )

    @patch("openai.AsyncClient")
    async def test_audio_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Non-stream audio-only output yields transcript TextBlock + audio
        DataBlock."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                text=None,
                audio={
                    "data": "QUJDREVG",
                    "transcript": "Hello from audio.",
                },
            ),
        )
        mock_client_cls.return_value.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    TextBlock.model_construct(
                        id=A,
                        text="Hello from audio.",
                    ),
                    DataBlock.model_construct(
                        id=A,
                        source=Base64Source.model_construct(
                            type="base64",
                            media_type="audio/wav",
                            data="QUJDREVG",
                        ),
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
                text="The answer is 42.",
                reasoning="Let me think step by step...",
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
                        thinking="Let me think step by step...",
                    ),
                    TextBlock.model_construct(id=A, text="The answer is 42."),
                ],
            ),
        )


# ---------------------------------------------------------------------------
# Streaming tests
# ---------------------------------------------------------------------------


class TestOpenAIChatStream(IsolatedAsyncioTestCase):
    """Tests for OpenAIChatModel in streaming mode."""

    def setUp(self) -> None:
        self.model = _make_model(stream=True)

    @patch("openai.AsyncClient")
    async def test_stream_text_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream text yields n deltas (is_last=False) + 1 final
        (is_last=True) with full content."""
        chunks = [
            _make_stream_chunk(delta_text="Hello"),
            _make_stream_chunk(delta_text=" world"),
            _make_stream_chunk(delta_text="!"),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 10, "completion_tokens": 3},
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
                (False, [TextBlock.model_construct(id=A, text="!")]),
                (True, [TextBlock.model_construct(id=A, text="Hello world!")]),
            ],
        )
        self.assertEqual(responses[-1].id, "resp-1")

    @patch("openai.AsyncClient")
    async def test_stream_thinking_and_text(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream with thinking + text yields deltas then final with both."""
        chunks = [
            _make_stream_chunk(delta_reasoning="Think"),
            _make_stream_chunk(delta_reasoning="ing..."),
            _make_stream_chunk(delta_text="Answer"),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 10, "completion_tokens": 8},
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
                (
                    False,
                    [ThinkingBlock.model_construct(id=A, thinking="ing...")],
                ),
                (False, [TextBlock.model_construct(id=A, text="Answer")]),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            thinking="Thinking...",
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
        """Stream tool calls accumulate across chunks into final response."""
        chunks = [
            _make_stream_chunk(
                tool_calls=[
                    _make_tool_call_delta(0, "call-1", "get_weather", '{"ci'),
                ],
            ),
            _make_stream_chunk(
                tool_calls=[
                    _make_tool_call_delta(0, None, None, 'ty":"BJ"}'),
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
                            name="get_weather",
                            input='{"ci',
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ToolCallBlock(
                            id="call-1",
                            name="get_weather",
                            input='ty":"BJ"}',
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ToolCallBlock(
                            id="call-1",
                            name="get_weather",
                            input='{"city":"BJ"}',
                        ),
                    ],
                ),
            ],
        )

    @patch("openai.AsyncClient")
    async def test_stream_usage_in_final(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Usage information is captured and present in final response."""
        chunks = [
            _make_stream_chunk(delta_text="Hi"),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 100, "completion_tokens": 20},
            ),
        ]
        mock_create = AsyncMock(return_value=_MockAsyncStream(chunks))
        mock_client_cls.return_value.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (False, [TextBlock.model_construct(id=A, text="Hi")]),
                (True, [TextBlock.model_construct(id=A, text="Hi")]),
            ],
        )
        self.assertEqual(responses[-1].usage.input_tokens, 100)
        self.assertEqual(responses[-1].usage.output_tokens, 20)

    @patch("openai.AsyncClient")
    async def test_stream_audio_response(
        self,
        mock_client_cls: MagicMock,
    ) -> None:
        """Stream PCM deltas produce per-chunk DataBlocks (first chunk
        prefixed with a streaming WAV header) sharing a stable id, plus a
        final fixed-size WAV block readable by the ``wave`` module.
        Transcript chunks ride alongside as TextBlock deltas so the agent
        can stream caption text live; the final block carries the full
        accumulated transcript."""
        pcm1 = bytes([1, 2, 3, 4])
        pcm2 = bytes([5, 6, 7, 8])
        pcm3 = bytes([9, 10, 11, 12])
        pcm_full = pcm1 + pcm2 + pcm3

        chunks = [
            _make_stream_chunk(
                delta_audio={
                    "data": base64.b64encode(pcm1).decode(),
                    "transcript": "Hello",
                },
            ),
            _make_stream_chunk(
                delta_audio={
                    "data": base64.b64encode(pcm2).decode(),
                    "transcript": " world",
                },
            ),
            _make_stream_chunk(
                delta_audio={
                    "data": base64.b64encode(pcm3).decode(),
                    "transcript": "!",
                },
            ),
            _make_stream_chunk(
                has_choices=False,
                usage={"prompt_tokens": 10, "completion_tokens": 6},
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
        first_audio = next(
            b for b in responses[0].content if isinstance(b, DataBlock)
        )
        first_payload = base64.b64decode(first_audio.source.data)
        self.assertEqual(len(first_payload), 44 + len(pcm1))
        self.assertEqual(first_payload[:4], b"RIFF")
        self.assertEqual(first_payload[8:12], b"WAVE")
        self.assertEqual(first_payload[44:], pcm1)
        self.assertEqual(first_audio.source.media_type, "audio/wav")

        # Subsequent deltas: raw PCM only, no header.
        for resp, pcm in zip(responses[1:3], [pcm2, pcm3]):
            audio_block = next(
                b for b in resp.content if isinstance(b, DataBlock)
            )
            self.assertEqual(base64.b64decode(audio_block.source.data), pcm)
            self.assertEqual(audio_block.source.media_type, "audio/wav")

        # Transcript rides alongside: each delta carries a TextBlock with
        # only that chunk's text (so the agent emits TextBlockDeltaEvents
        # in real time).
        for resp, expected_text in zip(
            responses[:3],
            ["Hello", " world", "!"],
        ):
            text_block = next(
                b for b in resp.content if isinstance(b, TextBlock)
            )
            self.assertEqual(text_block.text, expected_text)

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

        # Transcript is accumulated and emitted as a TextBlock alongside.
        final_text = next(b for b in final.content if isinstance(b, TextBlock))
        self.assertEqual(final_text.text, "Hello world!")


class TestOpenAIChatModelParameters(unittest.TestCase):
    """Tests for OpenAIChatModel.Parameters."""

    def test_reasoning_effort_stored_on_model(self) -> None:
        """reasoning_effort is accessible through model.parameters."""
        model = OpenAIChatModel(
            credential=OpenAICredential(api_key="test"),
            model="o3",
            stream=False,
            context_size=200_000,
            parameters=OpenAIChatModel.Parameters(reasoning_effort="low"),
        )
        self.assertEqual(model.parameters.reasoning_effort, "low")

    def test_thinking_enable_stored_on_model(self) -> None:
        """thinking_enable is accessible through model.parameters."""
        model = OpenAIChatModel(
            credential=OpenAICredential(api_key="test"),
            model="o3",
            stream=False,
            context_size=200_000,
            parameters=OpenAIChatModel.Parameters(thinking_enable=True),
        )
        self.assertTrue(model.parameters.thinking_enable)


# ---------------------------------------------------------------------------
# Shared _format_tools fixtures
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


class TestOpenAIChatFormatTools(unittest.TestCase):
    """Tests for OpenAIChatModel._format_tools."""

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

    def test_required_mode(self) -> None:
        """Required mode returns tools unchanged and string 'required'."""
        fmt_tools, fmt_choice = self.model._format_tools(
            _FT_TOOLS,
            ToolChoice(mode="required"),
        )
        self.assertEqual(fmt_tools, _FT_TOOLS)
        self.assertEqual(fmt_choice, "required")

    def test_str_mode_force_call(self) -> None:
        """A specific tool name returns a type=function dict."""
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
