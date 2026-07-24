# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the TTS module.

Covers:
  * ``TTSModelBase`` default no-op behaviour for ``connect`` / ``close`` /
    ``push`` (so non-realtime subclasses needn't override them).
  * ``DashScopeTTSModel`` non-streaming aggregation.
  * ``DashScopeTTSModel`` streaming: incremental deltas and ``is_last``
    placement at the final chunk only.
  * ``DashScopeCosyVoiceTTSModel`` WebSocket TTS streaming and non-streaming
    output over a mocked SDK.
  * ``DashScopeRealtimeTTSModel`` connect / close / push / synthesize
    lifecycle over a mocked WebSocket.
  * ``DashScopeCosyVoiceTTSModel`` realtime connect / close / push /
    synthesize lifecycle over a mocked SpeechSynthesizer.
"""
import base64
import io
import wave
from typing import Any, AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, Mock, patch

from agentscope.app._service._tts_model import _resolve_tts_class
from agentscope.credential import DashScopeCredential
from agentscope.tts import (
    DashScopeCosyVoiceTTSModel,
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
        """Return a minimal response for base-class lifecycle tests."""
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
        """Record realtime context-manager connect calls."""
        self.connect_calls += 1

    async def close(self) -> None:
        """Record realtime context-manager close calls."""
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
# DashScopeCosyVoiceTTSModel — WebSocket TTS streaming and aggregation
# ---------------------------------------------------------------------------


class TestDashScopeCosyVoiceTTSModel(IsolatedAsyncioTestCase):
    """The unittests for DashScope CosyVoice TTS model."""

    def setUp(self) -> None:
        """Set up the test case."""
        self.patcher = patch(
            "dashscope.audio.tts_v2.SpeechSynthesizer",
        )
        self.mock_synthesizer_class = self.patcher.start()
        self.synthesizer_instances: list[Mock] = []
        self.next_audio = b"FULL_AUDIO"
        self.next_chunks = [b"CHUNK"]
        self.mock_synthesizer_class.side_effect = self._make_synthesizer

    def tearDown(self) -> None:
        """Tear down the test case."""
        self.patcher.stop()

    def _make_synthesizer(self, **init_kwargs: Any) -> Mock:
        """Create a mocked tts_v2 SpeechSynthesizer instance."""
        synth = Mock()
        synth.init_kwargs = init_kwargs
        callback = init_kwargs.get("callback")

        if callback is None:
            synth.call = Mock(return_value=self.next_audio)
        else:

            def _call(text: str, **kwargs: Any) -> None:
                del text, kwargs
                for chunk in self.next_chunks:
                    callback.on_data(chunk)
                callback.on_complete()
                callback.on_close()

            synth.call = Mock(side_effect=_call)

        self.synthesizer_instances.append(synth)
        return synth

    def _make_model(
        self,
        stream: bool = False,
    ) -> DashScopeCosyVoiceTTSModel:
        """Create a DashScopeCosyVoiceTTSModel with test credentials."""
        return DashScopeCosyVoiceTTSModel(
            credential=DashScopeCredential(api_key="test"),
            model="cosyvoice-v3-flash",
            parameters=DashScopeCosyVoiceTTSModel.Parameters(
                voice="longanhuan",
            ),
            stream=stream,
        )

    async def test_empty_string_short_circuits(self) -> None:
        """``synthesize("")`` returns empty without touching the SDK."""
        model = self._make_model(stream=False)

        result = await model.synthesize("")

        self.assertIsNone(result.content)
        self.mock_synthesizer_class.assert_not_called()

    async def test_non_stream_returns_full_audio(
        self,
    ) -> None:
        """stream=False returns one full audio payload from the SDK."""
        self.next_audio = b"AAAABBBB"
        model = self._make_model(stream=False)

        result = await model.synthesize("Hello")

        self.assertIsInstance(result, TTSResponse)
        self.assertEqual(result.content.source.media_type, _MEDIA_TYPE)
        wav_payload = base64.b64decode(result.content.source.data)
        with wave.open(io.BytesIO(wav_payload), "rb") as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getframerate(), 24000)
            self.assertEqual(wav.readframes(wav.getnframes()), b"AAAABBBB")

        synth = self.synthesizer_instances[-1]
        init_kwargs = synth.init_kwargs
        self.assertEqual(init_kwargs["model"], "cosyvoice-v3-flash")
        self.assertEqual(init_kwargs["voice"], "longanhuan")
        self.assertEqual(init_kwargs["format"].format, "pcm")
        self.assertEqual(init_kwargs["format"].sample_rate, 24000)
        synth.call.assert_called_once_with(text="Hello")

    async def test_streaming_returns_callback_audio_chunks(
        self,
    ) -> None:
        """stream=True returns callback audio chunks."""
        self.next_chunks = [b"AAAA", b"BBBB"]
        model = self._make_model(stream=True)

        gen = await model.synthesize("Hello")
        chunks = [c async for c in gen]

        self.assertEqual(len(chunks), 1)
        payload = base64.b64decode(chunks[0].content.source.data)
        self.assertTrue(payload.startswith(b"RIFF"))
        self.assertTrue(payload.endswith(b"AAAABBBB"))
        self.assertEqual([c.is_last for c in chunks], [True])

        synth = self.synthesizer_instances[-1]
        self.assertIsNotNone(synth.init_kwargs["callback"])
        synth.call.assert_called_once_with(text="Hello")

    async def test_model_cards_and_credential_wiring(self) -> None:
        """CosyVoice model cards are discovered only by the CosyVoice class,
        and DashScope credentials expose the class."""
        cosyvoice_cards = DashScopeCosyVoiceTTSModel.list_models()
        cosyvoice_names = {card.name for card in cosyvoice_cards}
        self.assertGreater(len(cosyvoice_cards), 0)
        self.assertIn("cosyvoice-v3-flash", cosyvoice_names)
        self.assertIn("cosyvoice-v3-plus", cosyvoice_names)
        self.assertTrue(all(not card.realtime for card in cosyvoice_cards))
        for card in cosyvoice_cards:
            properties = card.parameter_schema["properties"]
            self.assertIn("voice", properties)
            self.assertIn("realtime", properties)
        plus_card = next(
            card
            for card in cosyvoice_cards
            if card.name == "cosyvoice-v3-plus"
        )
        self.assertIn(
            "longanhuan",
            plus_card.parameter_schema["properties"]["voice"]["enum"],
        )

        qwen_names = {card.name for card in DashScopeTTSModel.list_models()}
        self.assertTrue(cosyvoice_names.isdisjoint(qwen_names))

        credential_classes = DashScopeCredential.get_tts_model_classes()
        self.assertIn(DashScopeCosyVoiceTTSModel, credential_classes)
        credential_names = {
            card.name for card in DashScopeCredential.list_tts_models()
        }
        self.assertIn("cosyvoice-v3-flash", credential_names)
        self.assertIn("cosyvoice-v3-plus", credential_names)

        resolved_without_realtime = _resolve_tts_class(
            credential_classes,
            "cosyvoice-v3-plus",
        )
        self.assertIs(
            resolved_without_realtime,
            DashScopeCosyVoiceTTSModel,
        )


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

    async def test_async_context_manager(self) -> None:
        """``async with`` triggers connect on enter and close on exit."""
        with patch.dict("sys.modules", self.mock_modules):
            model = self._make_model()
            async with model:
                self.assertTrue(model._connected)
            self.assertFalse(model._connected)

    # -- push --

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
                self.assertEqual(model._accumulated_text, "Hello world")

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

    # -- callback --

    async def test_get_audio_chunks_prepends_header_without_prior_push(
        self,
    ) -> None:
        """get_audio_chunks() prepends a WAV header when push() has not
        yet consumed any bytes (_consumed == 0)."""
        with patch.dict("sys.modules", self.mock_modules):
            from agentscope.tts._dashscope._realtime_model import (
                _make_callback_class,
            )

            callback_cls = _make_callback_class()
            cb = callback_cls()

            audio_b64 = base64.b64encode(b"ALLPCM").decode()
            cb.on_event({"type": "response.audio.delta", "delta": audio_b64})
            cb.on_event({"type": "session.finished"})

            chunks = [c async for c in cb.get_audio_chunks()]
            raw_payloads = [
                base64.b64decode(c.content.source.data)
                for c in chunks
                if c.content is not None
            ]

            self.assertTrue(len(raw_payloads) > 0)
            self.assertTrue(raw_payloads[0].startswith(b"RIFF"))
            self.assertTrue(raw_payloads[0].endswith(b"ALLPCM"))

    async def test_get_audio_chunks_skips_header_after_partial_push(
        self,
    ) -> None:
        """get_audio_chunks() does NOT prepend a second WAV header when
        push() already consumed and sent the first chunk with a header
        (_consumed > 0)."""
        with patch.dict("sys.modules", self.mock_modules):
            from agentscope.tts._dashscope._realtime_model import (
                _make_callback_class,
            )

            callback_cls = _make_callback_class()
            cb = callback_cls()

            # Simulate push() consuming the first chunk (with WAV header)
            first_b64 = base64.b64encode(b"FIRSTPCM").decode()
            cb.on_event(
                {"type": "response.audio.delta", "delta": first_b64},
            )
            first_resp = cb.get_audio_response(block=False)
            self.assertIsNotNone(first_resp.content)
            self.assertGreater(cb._consumed, 0)

            # More data arrives and session finishes
            more_b64 = base64.b64encode(b"MOREPCM").decode()
            cb.on_event(
                {"type": "response.audio.delta", "delta": more_b64},
            )
            cb.on_event({"type": "session.finished"})

            chunks = [c async for c in cb.get_audio_chunks()]
            raw_payloads = [
                base64.b64decode(c.content.source.data)
                for c in chunks
                if c.content is not None
            ]

            self.assertTrue(len(raw_payloads) > 0)
            # No second RIFF header should appear mid-stream
            self.assertFalse(raw_payloads[0].startswith(b"RIFF"))
            self.assertEqual(raw_payloads[0], b"MOREPCM")

    async def test_get_audio_response_includes_header_without_prior_push(
        self,
    ) -> None:
        """get_audio_response() prepends a WAV header when no data has been
        consumed yet (_consumed == 0)."""
        with patch.dict("sys.modules", self.mock_modules):
            from agentscope.tts._dashscope._realtime_model import (
                _make_callback_class,
            )

            callback_cls = _make_callback_class()
            cb = callback_cls()

            audio_b64 = base64.b64encode(b"ALLPCM").decode()
            cb.on_event({"type": "response.audio.delta", "delta": audio_b64})
            cb.on_event({"type": "session.finished"})

            resp = cb.get_audio_response(block=True)
            raw = base64.b64decode(resp.content.source.data)

            self.assertTrue(raw.startswith(b"RIFF"))
            self.assertTrue(raw.endswith(b"ALLPCM"))

    async def test_get_audio_response_skips_header_after_partial_push(
        self,
    ) -> None:
        """get_audio_response() does NOT prepend a second WAV header when
        push() already consumed and sent the first chunk with a header
        (_consumed > 0)."""
        with patch.dict("sys.modules", self.mock_modules):
            from agentscope.tts._dashscope._realtime_model import (
                _make_callback_class,
            )

            callback_cls = _make_callback_class()
            cb = callback_cls()

            first_b64 = base64.b64encode(b"FIRSTPCM").decode()
            cb.on_event(
                {"type": "response.audio.delta", "delta": first_b64},
            )
            first_resp = cb.get_audio_response(block=False)
            self.assertIsNotNone(first_resp.content)
            self.assertGreater(cb._consumed, 0)

            more_b64 = base64.b64encode(b"MOREPCM").decode()
            cb.on_event(
                {"type": "response.audio.delta", "delta": more_b64},
            )
            cb.on_event({"type": "session.finished"})

            resp = cb.get_audio_response(block=True)
            raw = base64.b64decode(resp.content.source.data)

            self.assertFalse(raw.startswith(b"RIFF"))
            self.assertEqual(raw, b"MOREPCM")


# ---------------------------------------------------------------------------
# DashScopeCosyVoiceTTSModel — realtime push / synthesize lifecycle
# ---------------------------------------------------------------------------


# pylint: disable=too-many-public-methods
class TestDashScopeCosyVoiceRealtimeMode(
    IsolatedAsyncioTestCase,
):
    """Unit tests for CosyVoice realtime mode."""

    def setUp(self) -> None:
        self.mock_modules = self._create_mock_cosyvoice_modules()
        self.mock_synthesizer = self._create_mock_synthesizer()
        self.mock_modules["dashscope.audio.tts_v2"].SpeechSynthesizer = Mock(
            return_value=self.mock_synthesizer,
        )

    @staticmethod
    def _create_mock_cosyvoice_modules() -> dict:
        mock_result_callback = Mock
        mock_audio_format = MagicMock()
        mock_audio_format.PCM_24000HZ_MONO_16BIT = "pcm_24000hz_mono_16bit"

        mock_tts_v2 = MagicMock()
        mock_tts_v2.ResultCallback = mock_result_callback
        mock_tts_v2.AudioFormat = mock_audio_format
        mock_tts_v2.SpeechSynthesizer = Mock

        mock_audio = MagicMock()
        mock_audio.tts_v2 = mock_tts_v2

        mock_dashscope = MagicMock()
        mock_dashscope.api_key = None
        mock_dashscope.audio = mock_audio

        return {
            "dashscope": mock_dashscope,
            "dashscope.audio": mock_audio,
            "dashscope.audio.tts_v2": mock_tts_v2,
        }

    @staticmethod
    def _create_mock_synthesizer() -> Mock:
        synth = Mock()
        synth.streaming_call = Mock()
        synth.streaming_complete = Mock()
        synth.close = Mock()
        return synth

    def _make_model(
        self,
        **kwargs: Any,
    ) -> DashScopeCosyVoiceTTSModel:
        defaults: dict[str, Any] = {
            "credential": DashScopeCredential(api_key="test"),
            "model": "cosyvoice-v3-plus",
            "parameters": DashScopeCosyVoiceTTSModel.Parameters(
                voice="longanhuan",
                realtime=True,
            ),
            "stream": True,
            "max_retries": 1,
            "retry_delay": 0.0,
        }
        defaults.update(kwargs)
        return DashScopeCosyVoiceTTSModel(**defaults)

    def _mock_synthesize_callback(self, model: Any) -> None:
        """Set up callback mocks so synthesize() doesn't block."""
        model._callback.finish_event = Mock()
        model._callback.finish_event.wait = Mock(return_value=True)
        model._callback.has_audio_data = Mock(return_value=True)
        model._callback.get_audio_response = Mock(
            return_value=TTSResponse(content=None),
        )

    # -- connect / close --

    async def test_async_context_manager(self) -> None:
        """async with triggers connect on enter and close on exit."""
        with patch.dict("sys.modules", self.mock_modules):
            model = self._make_model()
            async with model:
                self.assertTrue(model._connected)
            self.assertFalse(model._connected)

    async def test_async_context_skips_connect_when_not_realtime(self) -> None:
        """async with does not connect when the realtime parameter is off."""
        with patch.dict("sys.modules", self.mock_modules):
            model = self._make_model(
                parameters=DashScopeCosyVoiceTTSModel.Parameters(
                    voice="longanhuan",
                    realtime=False,
                ),
            )
            async with model:
                self.assertFalse(model._connected)
            self.mock_modules[
                "dashscope.audio.tts_v2"
            ].SpeechSynthesizer.assert_not_called()

    # -- push --

    async def test_push_incremental_deltas(self) -> None:
        """Consecutive deltas are each forwarded via streaming_call."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                model._callback.get_audio_response = Mock(
                    return_value=TTSResponse(content=None),
                )
                await model.push("Hello")
                await model.push(" world")

                self.assertEqual(
                    self.mock_synthesizer.streaming_call.call_count,
                    2,
                )
                self.mock_synthesizer.streaming_call.assert_any_call("Hello")
                self.mock_synthesizer.streaming_call.assert_any_call(" world")

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

    async def test_push_cold_start_buffers_across_deltas(self) -> None:
        """Multiple small deltas are buffered until cold_start_length is
        met, then flushed as a single streaming_call."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(cold_start_length=10) as model:
                model._callback.get_audio_response = Mock(
                    return_value=TTSResponse(content=None),
                )
                await model.push("Hi")
                await model.push(" there")
                self.mock_synthesizer.streaming_call.assert_not_called()
                self.assertFalse(model._cold_start_done)

                await model.push(" friend!")
                self.mock_synthesizer.streaming_call.assert_called_once_with(
                    "Hi there friend!",
                )
                self.assertTrue(model._cold_start_done)

    async def test_push_after_cold_start_forwards_directly(self) -> None:
        """Once cold start is done, subsequent deltas bypass the buffer."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(cold_start_length=3) as model:
                model._callback.get_audio_response = Mock(
                    return_value=TTSResponse(content=None),
                )
                await model.push("Hello")
                self.mock_synthesizer.streaming_call.assert_called_with(
                    "Hello",
                )

                await model.push(" world")
                self.mock_synthesizer.streaming_call.assert_called_with(
                    " world",
                )
                self.assertEqual(
                    self.mock_synthesizer.streaming_call.call_count,
                    2,
                )

    async def test_push_cold_start_words_buffers(self) -> None:
        """Deltas below cold_start_words are buffered."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(cold_start_words=3) as model:
                model._callback.get_audio_response = Mock(
                    return_value=TTSResponse(content=None),
                )
                await model.push("Hello")
                await model.push(" world")

                self.mock_synthesizer.streaming_call.assert_not_called()
                self.assertFalse(model._cold_start_done)

    async def test_push_exception_returns_empty(self) -> None:
        """push() returns empty response if streaming_call raises."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                self.mock_synthesizer.streaming_call.side_effect = Exception(
                    "connection error",
                )
                res = await model.push("Hello")

                self.assertIsNone(res.content)

    # -- synthesize --

    async def test_synthesize_calls_streaming_complete(self) -> None:
        """synthesize() calls streaming_call and streaming_complete."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                self._mock_synthesize_callback(model)

                await model.synthesize(text="Hello")

                self.mock_synthesizer.streaming_call.assert_called_once_with(
                    "Hello",
                )
                self.mock_synthesizer.streaming_complete.assert_called_once()

    async def test_synthesize_appends_extra_text(self) -> None:
        """synthesize(text=...) after push appends the extra text."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                model._callback.get_audio_response = Mock(
                    return_value=TTSResponse(content=None),
                )
                await model.push("Hello")
                self.mock_synthesizer.streaming_call.reset_mock()

                self._mock_synthesize_callback(model)
                await model.synthesize(text=" world")

                self.mock_synthesizer.streaming_call.assert_called_once_with(
                    " world",
                )
                self.mock_synthesizer.streaming_complete.assert_called_once()

    async def test_synthesize_no_text_drain(self) -> None:
        """synthesize(None) after push calls streaming_complete without
        extra streaming_call."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model() as model:
                model._callback.get_audio_response = Mock(
                    return_value=TTSResponse(content=None),
                )
                await model.push("Hello")
                self.mock_synthesizer.streaming_call.reset_mock()

                self._mock_synthesize_callback(model)
                await model.synthesize()

                self.mock_synthesizer.streaming_call.assert_not_called()
                self.mock_synthesizer.streaming_complete.assert_called_once()

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
        buffered text before calling streaming_complete."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(cold_start_length=100) as model:
                model._callback.get_audio_response = Mock(
                    return_value=TTSResponse(content=None),
                )
                await model.push("Hi")
                await model.push(" there")
                self.mock_synthesizer.streaming_call.assert_not_called()

                self._mock_synthesize_callback(model)
                await model.synthesize()

                self.mock_synthesizer.streaming_call.assert_called_once_with(
                    "Hi there",
                )
                self.mock_synthesizer.streaming_complete.assert_called_once()

    async def test_synthesize_empty_text_short_circuits(self) -> None:
        """synthesize() with no accumulated text returns empty."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(stream=True) as model:
                gen = await model.synthesize()
                chunks = [c async for c in gen]

                self.assertEqual(len(chunks), 1)
                self.assertIsNone(chunks[0].content)
                self.assertTrue(chunks[0].is_last)
                self.mock_synthesizer.streaming_complete.assert_not_called()

    async def test_synthesize_no_audio_raises_after_retries(self) -> None:
        """RuntimeError after max_retries with no audio received."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(
                max_retries=1,
                retry_delay=0.0,
            ) as model:
                model._callback.finish_event = Mock()
                model._callback.finish_event.wait = Mock(return_value=True)
                model._callback.has_audio_data = Mock(return_value=False)

                with self.assertRaises(RuntimeError):
                    await model.synthesize(text="Hello")

    async def test_synthesize_timeout_raises(self) -> None:
        """RuntimeError when finish_event.wait times out."""
        with patch.dict("sys.modules", self.mock_modules):
            async with self._make_model(
                max_retries=1,
                retry_delay=0.0,
            ) as model:
                model._callback.finish_event = Mock()
                model._callback.finish_event.wait = Mock(return_value=False)

                with self.assertRaises(RuntimeError) as ctx:
                    await model.synthesize(text="Hello")

                self.assertIn("timed out", str(ctx.exception))

    # -- callback --

    async def test_callback_on_complete_sets_finish_event(self) -> None:
        """on_complete() correctly sets finish_event and chunk_event."""
        with patch.dict("sys.modules", self.mock_modules):
            from agentscope.tts._dashscope._cosyvoice_utils import (
                _make_cosyvoice_callback_class,
            )

            callback_cls = _make_cosyvoice_callback_class()
            cb = callback_cls()

            self.assertFalse(cb.finish_event.is_set())
            self.assertFalse(cb.chunk_event.is_set())

            cb.on_complete()

            self.assertTrue(cb.finish_event.is_set())
            self.assertTrue(cb.chunk_event.is_set())

    async def test_callback_on_data_accumulates(self) -> None:
        """on_data() accumulates PCM bytes and signals chunk_event."""
        with patch.dict("sys.modules", self.mock_modules):
            from agentscope.tts._dashscope._cosyvoice_utils import (
                _make_cosyvoice_callback_class,
            )

            callback_cls = _make_cosyvoice_callback_class()
            cb = callback_cls()

            cb.on_data(b"AAAA")
            cb.on_data(b"BBBB")

            self.assertEqual(bytes(cb._pcm_bytes), b"AAAABBBB")
            self.assertTrue(cb.chunk_event.is_set())

    async def test_callback_take_delta_incremental(self) -> None:
        """_take_delta() returns only new bytes since last call."""
        with patch.dict("sys.modules", self.mock_modules):
            from agentscope.tts._dashscope._cosyvoice_utils import (
                _make_cosyvoice_callback_class,
            )

            callback_cls = _make_cosyvoice_callback_class()
            cb = callback_cls()

            cb.on_data(b"AAAA")
            delta1 = cb._take_delta(header=False)
            self.assertEqual(delta1, b"AAAA")

            cb.on_data(b"BBBB")
            delta2 = cb._take_delta(header=False)
            self.assertEqual(delta2, b"BBBB")

            delta3 = cb._take_delta(header=False)
            self.assertIsNone(delta3)

    async def test_callback_take_delta_with_header(self) -> None:
        """_take_delta(header=True) prepends WAV header to first chunk."""
        with patch.dict("sys.modules", self.mock_modules):
            from agentscope.tts._dashscope._cosyvoice_utils import (
                _make_cosyvoice_callback_class,
            )

            callback_cls = _make_cosyvoice_callback_class()
            cb = callback_cls()

            cb.on_data(b"PCMPCM")
            delta = cb._take_delta(header=True)

            self.assertTrue(delta.startswith(b"RIFF"))
            self.assertIn(b"WAVE", delta[:12])
            self.assertTrue(delta.endswith(b"PCMPCM"))

    async def test_get_audio_chunks_prepends_header_without_prior_push(
        self,
    ) -> None:
        """get_audio_chunks() prepends a WAV header when push() has not
        yet consumed any bytes (_consumed == 0)."""
        with patch.dict("sys.modules", self.mock_modules):
            from agentscope.tts._dashscope._cosyvoice_utils import (
                _make_cosyvoice_callback_class,
            )

            callback_cls = _make_cosyvoice_callback_class()
            cb = callback_cls()

            cb.on_data(b"ALLPCM")
            cb.on_complete()

            chunks = [c async for c in cb.get_audio_chunks()]
            raw_payloads = [
                base64.b64decode(c.content.source.data)
                for c in chunks
                if c.content is not None
            ]

            self.assertTrue(len(raw_payloads) > 0)
            self.assertTrue(raw_payloads[0].startswith(b"RIFF"))
            self.assertTrue(raw_payloads[0].endswith(b"ALLPCM"))

    async def test_get_audio_chunks_skips_header_after_partial_push(
        self,
    ) -> None:
        """get_audio_chunks() does NOT prepend a second WAV header when
        push() already consumed and sent the first chunk with a header
        (_consumed > 0)."""
        with patch.dict("sys.modules", self.mock_modules):
            from agentscope.tts._dashscope._cosyvoice_utils import (
                _make_cosyvoice_callback_class,
            )

            callback_cls = _make_cosyvoice_callback_class()
            cb = callback_cls()

            # Simulate push() consuming the first chunk (with WAV header)
            cb.on_data(b"FIRSTPCM")
            first_resp = cb.get_audio_response(block=False)
            self.assertIsNotNone(first_resp.content)
            self.assertGreater(cb._consumed, 0)

            # More data arrives and synthesis completes
            cb.on_data(b"MOREPCM")
            cb.on_complete()

            chunks = [c async for c in cb.get_audio_chunks()]
            raw_payloads = [
                base64.b64decode(c.content.source.data)
                for c in chunks
                if c.content is not None
            ]

            self.assertTrue(len(raw_payloads) > 0)
            # No second RIFF header should appear mid-stream
            self.assertFalse(raw_payloads[0].startswith(b"RIFF"))
            self.assertEqual(raw_payloads[0], b"MOREPCM")

    async def test_get_audio_response_includes_header_without_prior_push(
        self,
    ) -> None:
        """get_audio_response() prepends a WAV header when no data has been
        consumed yet (_consumed == 0)."""
        with patch.dict("sys.modules", self.mock_modules):
            from agentscope.tts._dashscope._cosyvoice_utils import (
                _make_cosyvoice_callback_class,
            )

            callback_cls = _make_cosyvoice_callback_class()
            cb = callback_cls()

            cb.on_data(b"ALLPCM")
            cb.on_complete()

            resp = cb.get_audio_response(block=True)
            raw = base64.b64decode(resp.content.source.data)

            self.assertTrue(raw.startswith(b"RIFF"))
            self.assertTrue(raw.endswith(b"ALLPCM"))

    async def test_get_audio_response_skips_header_after_partial_push(
        self,
    ) -> None:
        """get_audio_response() does NOT prepend a second WAV header when
        push() already consumed and sent the first chunk with a header
        (_consumed > 0)."""
        with patch.dict("sys.modules", self.mock_modules):
            from agentscope.tts._dashscope._cosyvoice_utils import (
                _make_cosyvoice_callback_class,
            )

            callback_cls = _make_cosyvoice_callback_class()
            cb = callback_cls()

            cb.on_data(b"FIRSTPCM")
            first_resp = cb.get_audio_response(block=False)
            self.assertIsNotNone(first_resp.content)
            self.assertGreater(cb._consumed, 0)

            cb.on_data(b"MOREPCM")
            cb.on_complete()

            resp = cb.get_audio_response(block=True)
            raw = base64.b64decode(resp.content.source.data)

            self.assertFalse(raw.startswith(b"RIFF"))
            self.assertEqual(raw, b"MOREPCM")

    async def test_callback_reset(self) -> None:
        """reset() clears all state."""
        with patch.dict("sys.modules", self.mock_modules):
            from agentscope.tts._dashscope._cosyvoice_utils import (
                _make_cosyvoice_callback_class,
            )

            callback_cls = _make_cosyvoice_callback_class()
            cb = callback_cls()

            cb.on_data(b"AAAA")
            cb.on_complete()
            cb.reset()

            self.assertFalse(cb.finish_event.is_set())
            self.assertFalse(cb.chunk_event.is_set())
            self.assertEqual(bytes(cb._pcm_bytes), b"")
            self.assertEqual(cb._consumed, 0)

    # -- reconnect --

    async def test_reconnect_recreates_synthesizer(self) -> None:
        """_reconnect() closes old synthesizer and creates a new one."""
        with patch.dict("sys.modules", self.mock_modules):
            model = self._make_model()
            await model.connect()
            old_callback = model._callback

            await model._reconnect()

            self.mock_synthesizer.close.assert_called_once()
            self.assertTrue(model._connected)
            self.assertIsNot(model._callback, old_callback)
