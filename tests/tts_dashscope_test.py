# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the TTS module.

Covers:
  * ``TTSModelBase`` default no-op behaviour for ``connect`` / ``close`` /
    ``push`` (so non-realtime subclasses needn't override them).
  * ``DashScopeTTSModel`` non-streaming aggregation.
  * ``DashScopeTTSModel`` streaming: incremental deltas and ``is_last``
    placement at the final chunk only.
  * ``DashScopeRealtimeTTSModel`` connect / close / push / synthesize
    lifecycle over a mocked WebSocket.
"""
import base64
import io
import wave
from typing import Any, AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, Mock, patch

from agentscope.credential import DashScopeCredential
from agentscope.tts import (
    DashScopeTTSModel,
    DashScopeRealtimeTTSModel,
    TTSModelBase,
    TTSResponse,
)


_MEDIA_TYPE = "audio/wav"
# DashScope TTS emits 24kHz / mono / 16-bit PCM; the WAV wrapping in the
# model layer uses the same parameters.
_TTS_SAMPLE_RATE = 24000
_TTS_CHANNELS = 1
_TTS_SAMPLE_WIDTH = 2  # bytes (= 16 bit)
_WAV_HEADER_LEN = 44


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api_chunk(
    data_bytes: bytes | None,
    usage: Any = None,
) -> MagicMock:
    """Build a chunk shaped like what dashscope.MultiModalConversation
    yields. ``data_bytes=None`` represents a chunk with no output."""
    chunk = MagicMock()
    chunk.usage = usage
    if data_bytes is None:
        chunk.output = None
        return chunk
    chunk.output = MagicMock()
    chunk.output.audio = MagicMock()
    chunk.output.audio.data = base64.b64encode(data_bytes).decode("ascii")
    return chunk


def _make_usage(
    input_tokens: int = 0,
    output_tokens: int = 0,
    characters: int = 0,
) -> MagicMock:
    """Build a usage object shaped like what the DashScope API returns."""
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    usage.characters = characters
    return usage


def _make_api_generator(chunks: list[bytes | None]) -> Any:
    """Build a sync generator like ``MultiModalConversation.call`` returns.
    The last chunk carries a usage object."""

    def _gen() -> Any:
        for i, data in enumerate(chunks):
            is_last = i == len(chunks) - 1
            usage = _make_usage(characters=10) if is_last else None
            yield _make_api_chunk(data, usage=usage)

    return _gen()


# ---------------------------------------------------------------------------
# TTSModelBase — default no-op surface for non-realtime subclasses
# ---------------------------------------------------------------------------


class _DummyTTS(TTSModelBase):
    """Minimal subclass that implements only ``synthesize`` — exercises the
    base class's no-op ``connect`` / ``close`` / ``push`` defaults."""

    async def synthesize(
        self,
        text: str | None = None,
        **kwargs: Any,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        del text, kwargs
        return TTSResponse(content=None)


class _RealtimeDummyTTS(_DummyTTS):
    """Realtime-flavoured dummy to assert ``__aenter__`` drives the lifecycle
    hooks when ``realtime`` is True."""

    realtime = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.connect_calls = 0
        self.close_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


def _make_dummy(cls: type = _DummyTTS) -> TTSModelBase:
    return cls(
        credential=DashScopeCredential(api_key="test"),
        model="x",
        stream=False,
    )


class TestTTSModelBaseDefaults(IsolatedAsyncioTestCase):
    """The base class supplies safe no-op defaults so non-realtime subclasses
    don't need to implement realtime-only hooks."""

    async def test_default_connect_close_noop(self) -> None:
        """Default connect/close return without raising."""
        model = _make_dummy()
        await model.connect()
        await model.close()

    async def test_default_push_returns_empty(self) -> None:
        """Default push returns an empty TTSResponse rather than raising,
        so a misuse on a non-realtime model degrades gracefully."""
        model = _make_dummy()
        resp = await model.push("ignored")
        self.assertIsNone(resp.content)

    async def test_aenter_skips_hooks_for_non_realtime(self) -> None:
        """``async with`` on a non-realtime model must not invoke connect/
        close (gated by ``realtime``)."""
        model = _make_dummy(_DummyTTS)
        async with model as m:
            self.assertIs(m, model)

    async def test_aenter_invokes_hooks_for_realtime(self) -> None:
        """For ``realtime=True`` subclasses, connect/close fire on
        enter/exit exactly once."""
        model = _make_dummy(_RealtimeDummyTTS)
        async with model:
            self.assertEqual(model.connect_calls, 1)
            self.assertEqual(model.close_calls, 0)
        self.assertEqual(model.close_calls, 1)


# ---------------------------------------------------------------------------
# DashScopeTTSModel — non-streaming and streaming
# ---------------------------------------------------------------------------


def _parse_wav_payload(wav_bytes: bytes) -> bytes:
    """Decode a full WAV file and return its raw PCM frames."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        return wav.readframes(wav.getnframes())


class TestDashScopeTTSModel(IsolatedAsyncioTestCase):
    """The unittests for DashScope TTS model (non-realtime)."""

    def setUp(self) -> None:
        """Set up the test case."""
        self.patcher = patch("dashscope.MultiModalConversation")
        self.mock_mmc = self.patcher.start()

    def tearDown(self) -> None:
        """Tear down the test case."""
        self.patcher.stop()

    def _make_model(self, stream: bool = False) -> DashScopeTTSModel:
        """Create a DashScopeTTSModel with test credentials."""
        return DashScopeTTSModel(
            credential=DashScopeCredential(api_key="test"),
            model="qwen3-tts-flash",
            parameters=DashScopeTTSModel.Parameters(voice="Cherry"),
            stream=stream,
        )

    # -- non-streaming --

    async def test_aggregates_chunks(self) -> None:
        """All API chunks are aggregated into one self-contained WAV."""
        self.mock_mmc.call.return_value = _make_api_generator(
            [b"AAAA", b"BBBB", b"CCCC"],
        )
        model = self._make_model(stream=False)

        result = await model.synthesize("Hello world")

        self.assertIsInstance(result, TTSResponse)
        self.assertEqual(result.content.source.media_type, _MEDIA_TYPE)
        wav_bytes = base64.b64decode(result.content.source.data)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            self.assertEqual(
                {
                    "framerate": wav.getframerate(),
                    "channels": wav.getnchannels(),
                    "sampwidth": wav.getsampwidth(),
                    "frames": wav.readframes(wav.getnframes()),
                },
                {
                    "framerate": _TTS_SAMPLE_RATE,
                    "channels": _TTS_CHANNELS,
                    "sampwidth": _TTS_SAMPLE_WIDTH,
                    "frames": b"AAAABBBBCCCC",
                },
            )
        self.assertTrue(result.is_last)

    async def test_none_short_circuits(self) -> None:
        """``synthesize(None)`` returns an empty response without touching
        the API."""
        model = self._make_model(stream=False)

        result = await model.synthesize(None)

        self.assertIsNone(result.content)
        self.mock_mmc.call.assert_not_called()

    async def test_empty_string_short_circuits(self) -> None:
        """``synthesize("")`` returns an empty response without touching
        the API."""
        model = self._make_model(stream=False)

        result = await model.synthesize("")

        self.assertIsNone(result.content)
        self.mock_mmc.call.assert_not_called()

    async def test_skips_empty_chunks(self) -> None:
        """Chunks without ``output`` are ignored during aggregation."""
        self.mock_mmc.call.return_value = _make_api_generator(
            [None, b"AAAA", None, b"BBBB"],
        )
        model = self._make_model(stream=False)

        result = await model.synthesize("Hello world")

        wav_bytes = base64.b64decode(result.content.source.data)
        self.assertEqual(_parse_wav_payload(wav_bytes), b"AAAABBBB")

    # -- streaming --

    async def test_incremental_deltas(self) -> None:
        """Each API chunk yields one TTSResponse with incremental PCM."""
        self.mock_mmc.call.return_value = _make_api_generator(
            [b"AAAA", b"BBBB", b"CCCC"],
        )
        model = self._make_model(stream=True)

        gen = await model.synthesize("Hello world")
        chunks = [c async for c in gen]

        payloads = [base64.b64decode(c.content.source.data) for c in chunks]

        self.assertTrue(payloads[0].startswith(b"RIFF"))
        self.assertEqual(payloads[0][8:12], b"WAVE")
        self.assertEqual(payloads[0][_WAV_HEADER_LEN:], b"AAAA")
        self.assertEqual(payloads[1], b"BBBB")
        self.assertEqual(payloads[2], b"CCCC")

        self.assertEqual(
            [c.is_last for c in chunks],
            [False, False, True],
        )
        self.assertEqual(
            [c.content.source.media_type for c in chunks],
            [_MEDIA_TYPE, _MEDIA_TYPE, _MEDIA_TYPE],
        )

    async def test_single_chunk_marked_last(self) -> None:
        """A lone audio chunk is flagged ``is_last=True`` with a streaming
        WAV header."""
        self.mock_mmc.call.return_value = _make_api_generator([b"ONLYCHUNK"])
        model = self._make_model(stream=True)

        gen = await model.synthesize("Hello world")
        chunks = [c async for c in gen]

        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].is_last)
        payload = base64.b64decode(chunks[0].content.source.data)
        self.assertTrue(payload.startswith(b"RIFF"))
        self.assertEqual(payload[_WAV_HEADER_LEN:], b"ONLYCHUNK")

    async def test_empty_stream_yields_terminal(self) -> None:
        """When the API yields no audio, the generator emits a terminal
        sentinel so consumers can detect EOS."""
        self.mock_mmc.call.return_value = _make_api_generator([None, None])
        model = self._make_model(stream=True)

        gen = await model.synthesize("Hello world")
        chunks = [c async for c in gen]

        self.assertEqual(len(chunks), 1)
        self.assertIsNone(chunks[0].content)
        self.assertTrue(chunks[0].is_last)


# ---------------------------------------------------------------------------
# DashScopeRealtimeTTSModel — realtime push / synthesize lifecycle
# ---------------------------------------------------------------------------


class TestDashScopeRealtimeTTSModel(  # pylint: disable=too-many-public-methods
    IsolatedAsyncioTestCase,
):
    """The unittests for DashScope Realtime TTS model."""

    def setUp(self) -> None:
        self.mock_modules = self._create_mock_dashscope_modules()
        self.mock_client = self._create_mock_tts_client()
        mock_tts_class = Mock(return_value=self.mock_client)
        self.mock_modules[
            "dashscope.audio.qwen_tts_realtime"
        ].QwenTtsRealtime = mock_tts_class

    @staticmethod
    def _create_mock_dashscope_modules() -> dict:
        mock_qwen_tts_realtime = MagicMock()
        mock_qwen_tts_realtime.QwenTtsRealtime = Mock
        mock_qwen_tts_realtime.QwenTtsRealtimeCallback = Mock

        mock_audio = MagicMock()
        mock_audio.qwen_tts_realtime = mock_qwen_tts_realtime

        mock_dashscope = MagicMock()
        mock_dashscope.api_key = None
        mock_dashscope.audio = mock_audio

        return {
            "dashscope": mock_dashscope,
            "dashscope.audio": mock_audio,
            "dashscope.audio.qwen_tts_realtime": mock_qwen_tts_realtime,
        }

    @staticmethod
    def _create_mock_tts_client() -> Mock:
        client = Mock()
        client.connect = Mock()
        client.close = Mock()
        client.finish = Mock()
        client.commit = Mock()
        client.update_session = Mock()
        client.append_text = Mock()
        return client

    def _make_model(self, **kwargs: Any) -> DashScopeRealtimeTTSModel:
        defaults: dict[str, Any] = {
            "credential": DashScopeCredential(api_key="test"),
            "model": "qwen3-tts-flash-realtime",
            "stream": True,
            "max_retries": 1,
            "retry_delay": 0.0,
        }
        defaults.update(kwargs)
        return DashScopeRealtimeTTSModel(**defaults)

    def _mock_synthesize_callback(self, model: Any) -> None:
        """Set up callback mocks so ``synthesize()`` doesn't block."""
        model._callback.finish_event = Mock()
        model._callback.finish_event.wait = Mock()
        model._callback.has_audio_data = Mock(return_value=True)
        model._callback.get_audio_response = Mock(
            return_value=TTSResponse(content=None),
        )

    # -- connect / close --

    async def test_connect_creates_client(self) -> None:
        """connect() creates a WebSocket client and calls update_session."""
        with patch.dict("sys.modules", self.mock_modules):
            model = self._make_model()
            await model.connect()

            self.assertTrue(model._connected)
            self.mock_client.connect.assert_called_once()
            self.mock_client.update_session.assert_called_once()

    async def test_close_disconnects(self) -> None:
        """close() sets _connected to False and closes the client."""
        with patch.dict("sys.modules", self.mock_modules):
            model = self._make_model()
            await model.connect()
            await model.close()

            self.assertFalse(model._connected)
            self.mock_client.close.assert_called_once()

    async def test_close_idempotent(self) -> None:
        """Calling close() when already disconnected is a no-op."""
        model = self._make_model()
        model._connected = False
        await model.close()
        self.assertFalse(model._connected)

    async def test_async_context_manager(self) -> None:
        """``async with`` triggers connect on enter and close on exit."""
        with patch.dict("sys.modules", self.mock_modules):
            model = self._make_model()
            async with model:
                self.assertTrue(model._connected)
            self.assertFalse(model._connected)

    # -- push --

    async def test_push_appends_text(self) -> None:
        """A single push forwards the delta to append_text."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                await model.push("Hello")

                self.mock_client.append_text.assert_called_once_with("Hello")
                self.assertEqual(model._accumulated_text, "Hello")
                self.assertTrue(model._cold_start_done)

    async def test_push_incremental_deltas(self) -> None:
        """Consecutive deltas are each forwarded verbatim."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                await model.push("Hello")
                await model.push(" world")

                self.assertEqual(
                    self.mock_client.append_text.call_count,
                    2,
                )
                self.mock_client.append_text.assert_any_call("Hello")
                self.mock_client.append_text.assert_any_call(" world")

    async def test_push_empty_text_no_call(self) -> None:
        """Empty string does not call append_text."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                res = await model.push("")

                self.mock_client.append_text.assert_not_called()
                self.assertIsNone(res.content)

    async def test_push_not_connected_raises(self) -> None:
        """push() raises RuntimeError when not connected."""
        model = self._make_model()
        model._connected = False

        with self.assertRaises(RuntimeError):
            await model.push("Hello")

    async def test_push_returns_audio_when_available(self) -> None:
        """push() returns audio data from the callback when available."""
        mock_audio_data = base64.b64encode(b"PCMDATA").decode("ascii")

        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                model._callback.get_audio_response = Mock(
                    return_value=TTSResponse(
                        content=MagicMock(
                            source=MagicMock(
                                data=mock_audio_data,
                                media_type=_MEDIA_TYPE,
                            ),
                        ),
                    ),
                )
                res = await model.push("Hello")

                self.assertIsNotNone(res.content)
                self.assertEqual(res.content.source.data, mock_audio_data)

    async def test_push_returns_empty_when_no_audio(self) -> None:
        """push() returns empty response when no audio is ready."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                model._callback.get_audio_response = Mock(
                    return_value=TTSResponse(content=None),
                )
                res = await model.push("Hello")
                self.assertIsNone(res.content)

    async def test_push_cold_start_buffers_across_deltas(self) -> None:
        """Multiple small deltas are buffered until cold_start_length is
        met, then flushed as a single ``append_text`` call."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(cold_start_length=10) as model:
                await model.push("Hi")
                await model.push(" there")
                self.mock_client.append_text.assert_not_called()
                self.assertFalse(model._cold_start_done)

                await model.push(" friend!")
                self.mock_client.append_text.assert_called_once_with(
                    "Hi there friend!",
                )
                self.assertTrue(model._cold_start_done)

    async def test_push_cold_start_single_delta_meets_threshold(self) -> None:
        """A single delta exceeding cold_start_length sends immediately."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(cold_start_length=5) as model:
                await model.push("Hello world")

                self.mock_client.append_text.assert_called_once_with(
                    "Hello world",
                )

    async def test_push_after_cold_start_forwards_directly(self) -> None:
        """Once cold start is done, subsequent deltas bypass the buffer."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(cold_start_length=3) as model:
                await model.push("Hello")
                self.mock_client.append_text.assert_called_with("Hello")

                await model.push(" world")
                self.mock_client.append_text.assert_called_with(" world")
                self.assertEqual(
                    self.mock_client.append_text.call_count,
                    2,
                )

    async def test_push_cold_start_words_buffers(self) -> None:
        """Deltas below cold_start_words are buffered."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(cold_start_words=3) as model:
                await model.push("Hello")
                await model.push(" world")

                self.mock_client.append_text.assert_not_called()
                self.assertFalse(model._cold_start_done)

    # -- synthesize --

    async def test_synthesize_commits_and_finishes(self) -> None:
        """synthesize(text=...) appends text, commits, and finishes."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                self._mock_synthesize_callback(model)

                await model.synthesize(text="Hello")

                self.mock_client.append_text.assert_called_once_with("Hello")
                self.mock_client.commit.assert_called_once()
                self.mock_client.finish.assert_called_once()

    async def test_synthesize_appends_extra_text(self) -> None:
        """synthesize(text=...) after push appends the extra text."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                model._callback.get_audio_response = Mock(
                    return_value=TTSResponse(content=None),
                )
                await model.push("Hello")
                self.mock_client.append_text.reset_mock()

                self._mock_synthesize_callback(model)
                await model.synthesize(text=" world")

                self.mock_client.append_text.assert_called_once_with(
                    " world",
                )
                self.mock_client.commit.assert_called_once()

    async def test_synthesize_no_text_drain(self) -> None:
        """synthesize(None) after push commits without extra append."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                model._callback.get_audio_response = Mock(
                    return_value=TTSResponse(content=None),
                )
                await model.push("Hello")
                self.mock_client.append_text.reset_mock()

                self._mock_synthesize_callback(model)
                await model.synthesize()

                self.mock_client.append_text.assert_not_called()
                self.mock_client.commit.assert_called_once()

    async def test_synthesize_resets_state(self) -> None:
        """State is reset after synthesize completes."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                self._mock_synthesize_callback(model)
                await model.synthesize(text="Hello")

                self.assertFalse(model._cold_start_done)
                self.assertEqual(model._accumulated_text, "")
                self.assertEqual(model._cold_start_buffer, "")

    async def test_synthesize_stream_returns_generator(self) -> None:
        """stream=True returns an async generator with is_last."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(stream=True) as model:
                self._mock_synthesize_callback(model)

                async def mock_chunks() -> AsyncGenerator[TTSResponse, None]:
                    yield TTSResponse(content=None, is_last=True)

                model._callback.get_audio_chunks = mock_chunks

                gen = await model.synthesize(text="Hello")
                chunks = [c async for c in gen]

                self.assertTrue(len(chunks) >= 1)
                self.assertTrue(chunks[-1].is_last)

    async def test_synthesize_non_stream_returns_single(self) -> None:
        """stream=False returns a single TTSResponse."""
        mock_audio_data = base64.b64encode(b"PCMDATA").decode("ascii")

        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(stream=False) as model:
                self._mock_synthesize_callback(model)
                model._callback.get_audio_response = Mock(
                    return_value=TTSResponse(
                        content=MagicMock(
                            source=MagicMock(
                                data=mock_audio_data,
                                media_type=_MEDIA_TYPE,
                            ),
                        ),
                    ),
                )

                res = await model.synthesize(text="Hello")

                self.assertIsInstance(res, TTSResponse)
                self.assertIsNotNone(res.content)

    async def test_synthesize_flushes_cold_start_buffer(self) -> None:
        """If cold start was never met during push, synthesize flushes the
        buffered text before committing."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(cold_start_length=100) as model:
                await model.push("Hi")
                await model.push(" there")
                self.mock_client.append_text.assert_not_called()

                self._mock_synthesize_callback(model)
                await model.synthesize()

                self.mock_client.append_text.assert_called_once_with(
                    "Hi there",
                )
                self.mock_client.commit.assert_called_once()

    async def test_synthesize_not_connected_raises(self) -> None:
        """synthesize() raises RuntimeError when not connected."""
        model = self._make_model()
        model._connected = False

        with self.assertRaises(RuntimeError):
            await model.synthesize(text="Hello")

    async def test_synthesize_no_audio_raises_after_retries(self) -> None:
        """RuntimeError after max_retries with no audio received."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(
                max_retries=1,
                retry_delay=0.0,
            ) as model:
                model._callback.finish_event = Mock()
                model._callback.finish_event.wait = Mock()
                model._callback.has_audio_data = Mock(return_value=False)

                with self.assertRaises(RuntimeError):
                    await model.synthesize(text="Hello")
