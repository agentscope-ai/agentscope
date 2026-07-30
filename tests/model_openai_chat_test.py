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
from unittest.mock import AsyncMock, MagicMock

from utils import AnyString

from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ThinkingBlock,
    DataBlock,
    Base64Source,
    UserMsg,
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
        # Client is built eagerly in __init__; inject a mock onto the
        # instance so create()/stream() calls hit it instead of the network.
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_text_response(self) -> None:
        """Non-stream text response returns a single ChatResponse."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello world!"),
        )
        self.mock_client.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    TextBlock.model_construct(
                        id=A,
                        created_at=A,
                        text="Hello world!",
                    ),
                ],
            ),
        )
        self.assertEqual(result.id, "resp-1")

    async def test_default_thinking_enable_not_forwarded(
        self,
    ) -> None:
        """Default parameters do not add provider-specific extra_body."""
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello world!"),
        )
        self.mock_client.chat.completions.create = mock_create

        await self.model([])

        self.assertNotIn("extra_body", mock_create.call_args.kwargs)

    async def test_constructor_extra_body_forwarded(
        self,
    ) -> None:
        """Custom request fields are forwarded to OpenAI-compatible APIs."""
        model = OpenAIChatModel(
            credential=OpenAICredential(api_key="test"),
            model="custom-model",
            stream=False,
            context_size=128_000,
            extra_body={"enable_thinking": False},
        )
        model.client = self.mock_client
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello world!"),
        )
        self.mock_client.chat.completions.create = mock_create

        await model([])

        self.assertEqual(
            mock_create.call_args.kwargs["extra_body"],
            {"enable_thinking": False},
        )

    async def test_generate_kwargs_extra_body_overrides_constructor(
        self,
    ) -> None:
        """Per-call extra_body overrides the constructor default."""
        model = OpenAIChatModel(
            credential=OpenAICredential(api_key="test"),
            model="custom-model",
            stream=False,
            context_size=128_000,
            extra_body={"enable_thinking": False},
        )
        model.client = self.mock_client
        mock_create = AsyncMock(
            return_value=_mock_completion(text="Hello world!"),
        )
        self.mock_client.chat.completions.create = mock_create

        await model([], extra_body={"custom_option": "value"})

        self.assertEqual(
            mock_create.call_args.kwargs["extra_body"],
            {"custom_option": "value"},
        )

    async def test_tool_call_response(
        self,
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
        self.mock_client.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ToolCallBlock.model_construct(
                        id="call-1",
                        created_at=A,
                        name="get_weather",
                        input='{"city":"Beijing"}',
                    ),
                ],
            ),
        )

    async def test_audio_response(
        self,
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
        self.mock_client.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    TextBlock.model_construct(
                        id=A,
                        created_at=A,
                        text="Hello from audio.",
                    ),
                    DataBlock.model_construct(
                        id=A,
                        created_at=A,
                        source=Base64Source.model_construct(
                            type="base64",
                            media_type="audio/wav",
                            data="QUJDREVG",
                        ),
                    ),
                ],
            ),
        )

    async def test_thinking_response(
        self,
    ) -> None:
        """Non-stream response with reasoning creates ThinkingBlock."""
        mock_create = AsyncMock(
            return_value=_mock_completion(
                text="The answer is 42.",
                reasoning="Let me think step by step...",
            ),
        )
        self.mock_client.chat.completions.create = mock_create

        result = await self.model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [
                    ThinkingBlock.model_construct(
                        id=A,
                        created_at=A,
                        thinking="Let me think step by step...",
                    ),
                    TextBlock.model_construct(
                        id=A,
                        created_at=A,
                        text="The answer is 42.",
                    ),
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
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_stream_text_response(
        self,
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
        self.mock_client.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text="Hello",
                        ),
                    ],
                ),
                (
                    False,
                    [
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text=" world",
                        ),
                    ],
                ),
                (
                    False,
                    [TextBlock.model_construct(id=A, created_at=A, text="!")],
                ),
                (
                    True,
                    [
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text="Hello world!",
                        ),
                    ],
                ),
            ],
        )
        self.assertEqual(responses[-1].id, "resp-1")

    async def test_stream_thinking_and_text(
        self,
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
        self.mock_client.chat.completions.create = mock_create

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
                            created_at=A,
                            thinking="Think",
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            created_at=A,
                            thinking="ing...",
                        ),
                    ],
                ),
                (
                    False,
                    [
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text="Answer",
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ThinkingBlock.model_construct(
                            id=A,
                            created_at=A,
                            thinking="Thinking...",
                        ),
                        TextBlock.model_construct(
                            id=A,
                            created_at=A,
                            text="Answer",
                        ),
                    ],
                ),
            ],
        )

    async def test_stream_tool_calls(
        self,
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
        self.mock_client.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [
                        ToolCallBlock.model_construct(
                            id="call-1",
                            created_at=A,
                            name="get_weather",
                            input='{"ci',
                        ),
                    ],
                ),
                (
                    False,
                    [
                        ToolCallBlock.model_construct(
                            id="call-1",
                            created_at=A,
                            name="get_weather",
                            input='ty":"BJ"}',
                        ),
                    ],
                ),
                (
                    True,
                    [
                        ToolCallBlock.model_construct(
                            id="call-1",
                            created_at=A,
                            name="get_weather",
                            input='{"city":"BJ"}',
                        ),
                    ],
                ),
            ],
        )

    async def test_stream_usage_in_final(
        self,
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
        self.mock_client.chat.completions.create = mock_create

        gen = await self.model([])
        responses = [r async for r in gen]

        self.assertListEqual(
            [(r.is_last, r.content) for r in responses],
            [
                (
                    False,
                    [TextBlock.model_construct(id=A, created_at=A, text="Hi")],
                ),
                (
                    True,
                    [TextBlock.model_construct(id=A, created_at=A, text="Hi")],
                ),
            ],
        )
        self.assertEqual(responses[-1].usage.input_tokens, 100)
        self.assertEqual(responses[-1].usage.output_tokens, 20)

    async def test_stream_audio_response(
        self,
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
        self.mock_client.chat.completions.create = mock_create

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


def _bad_request_error(message: str) -> Exception:
    """Build a minimal ``openai.BadRequestError``-compatible exception.

    The real SDK error is a thin subclass of ``APIError`` with a body +
    status code; for tests we only need ``str(exc)`` to contain the
    provided ``message`` and for ``isinstance(exc, openai.BadRequestError)``
    to be True, both of which this factory guarantees by importing
    from an installed ``openai`` SDK (shipped as a core dependency).
    """
    from openai import BadRequestError  # type: ignore[import-not-found]

    return BadRequestError(
        message=message,
        response=MagicMock(status_code=400),
        body={"error": {"message": message}},
    )


class TestToolChoiceRejectionFallback(IsolatedAsyncioTestCase):
    """Exercise ``_safe_chat_completions_create`` tool_choice retry loop."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)
        self.mock_client = MagicMock()
        self.model.client = self.mock_client

    async def test_400_tool_choice_rejection_retries_without_param(
        self,
    ) -> None:
        """On ``BadRequestError`` mentioning ``tool_choice`` the helper
        drops the ``tool_choice`` kwarg and retries exactly once."""
        calls = []
        good_response = _mock_completion(text="ok")

        async def create_side_effect(**kwargs: Any) -> Any:
            calls.append(dict(kwargs))
            if "tool_choice" in kwargs:
                raise _bad_request_error(
                    "deepseek-reasoner does not support this tool_choice",
                )
            return good_response

        self.mock_client.chat.completions.create = AsyncMock(
            side_effect=create_side_effect,
        )

        with self.assertWarns(UserWarning):
            result = await self.model._safe_chat_completions_create(
                {
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "tools": _FT_TOOLS,
                    "tool_choice": "required",
                },
            )

        self.assertIs(result, good_response)
        self.assertEqual(len(calls), 2)
        self.assertIn("tool_choice", calls[0])
        self.assertNotIn("tool_choice", calls[1])
        self.assertEqual(calls[1]["tools"], _FT_TOOLS)

    async def test_unrelated_400_does_not_retry(self) -> None:
        """A BadRequestError unrelated to tool_choice is re-raised."""
        from openai import BadRequestError  # type: ignore[import-not-found]

        async def _fail(**_kwargs: Any) -> Any:
            raise _bad_request_error(
                "invalid parameter: temperature must be <=2",
            )

        self.mock_client.chat.completions.create = AsyncMock(
            side_effect=_fail,
        )

        with self.assertRaises(BadRequestError):
            await self.model._safe_chat_completions_create(
                {
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "temperature": 3.5,
                },
            )

        # Only one attempt – no retrying unrelated errors
        self.assertEqual(
            self.mock_client.chat.completions.create.await_count,
            1,
        )

    async def test_second_attempt_failure_raises_first_error(self) -> None:
        """If the second attempt also fails, re-raise the original
        BadRequestError so operators see the provider message."""
        from openai import BadRequestError  # type: ignore[import-not-found]

        captured: list[Exception] = []

        async def _fail_both(**_kwargs: Any) -> Any:
            err = _bad_request_error(
                "reasoning models do not accept tool_choice at all",
            )
            captured.append(err)
            raise err

        self.mock_client.chat.completions.create = AsyncMock(
            side_effect=_fail_both,
        )

        with self.assertRaises(BadRequestError) as raised, self.assertWarns(
            UserWarning,
        ):
            await self.model._safe_chat_completions_create(
                {
                    "model": "deepseek-v4-pro",
                    "messages": [{"role": "user", "content": "Hi"}],
                    "tool_choice": "required",
                },
            )

        self.assertIs(raised.exception, captured[0])
        # Exactly two attempts: first with tool_choice, second without
        self.assertEqual(
            self.mock_client.chat.completions.create.await_count,
            2,
        )

    async def test_agent_invoke_path_observes_fallback(self) -> None:
        """End-to-end: ``__call__()`` with ``tool_choice`` triggers the
        fallback flow, drops the bad parameter, and returns a response
        instead of raising."""
        import warnings

        calls = []

        async def create_side_effect(**kwargs: Any) -> Any:
            calls.append({k for k in kwargs if "tool" in k})
            if "tool_choice" in kwargs:
                raise _bad_request_error(
                    "deepseek-reasoner does not support this tool_choice",
                )
            return _mock_completion(
                tool_calls=[
                    {
                        "id": "call-x",
                        "name": "get_weather",
                        "arguments": '{"city":"Shanghai"}',
                    },
                ],
            )

        self.mock_client.chat.completions.create = AsyncMock(
            side_effect=create_side_effect,
        )

        with warnings.catch_warnings(record=True) as captured_warnings:
            warnings.simplefilter("always")
            result = await self.model(
                [UserMsg(name="user", content="Shanghai weather?")],
                tools=_FT_TOOLS,
                tool_choice=ToolChoice(mode="required"),
            )

        self.assertTrue(result.is_last)
        blocks = [b for b in result.content if isinstance(b, ToolCallBlock)]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].name, "get_weather")

        self.assertEqual(len(calls), 2)
        self.assertIn("tool_choice", calls[0])
        self.assertNotIn("tool_choice", calls[1])
        self.assertTrue(
            any(
                "tool_choice parameter rejected" in str(w.message)
                for w in captured_warnings
            ),
        )
