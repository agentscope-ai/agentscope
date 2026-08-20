# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the Gemini TTS module.

Covers:
  * ``GeminiTTSModel`` non-streaming synthesis from ``generateContent``
    responses with ``responseModalities: ["AUDIO"]``.
  * Handling of empty/missing text and empty audio responses.
"""
import base64
import io
import wave
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.credential import GeminiCredential
from agentscope.tts import GeminiTTSModel, TTSResponse


_MEDIA_TYPE = "audio/wav"
# Gemini TTS emits 24kHz / mono / 16-bit PCM; the WAV wrapping in the model
# layer uses the same parameters.
_TTS_SAMPLE_RATE = 24000
_TTS_CHANNELS = 1
_TTS_SAMPLE_WIDTH = 2  # bytes (= 16 bit)


def _make_inline_data_part(data_bytes: bytes | None) -> MagicMock:
    """Build a response part shaped like what the Gemini ``generateContent``
    API returns for ``responseModalities: ["AUDIO"]``."""
    part = MagicMock()
    if data_bytes is None:
        part.inline_data = None
        return part
    part.inline_data = MagicMock()
    part.inline_data.data = base64.b64encode(data_bytes).decode("ascii")
    return part


def _make_usage_metadata(
    prompt_token_count: int = 0,
    candidates_token_count: int = 0,
) -> MagicMock:
    """Build a usage metadata object shaped like what the Gemini API
    returns."""
    usage = MagicMock()
    usage.prompt_token_count = prompt_token_count
    usage.candidates_token_count = candidates_token_count
    return usage


def _make_api_response(
    chunks: list[bytes | None],
    usage: Any = None,
) -> MagicMock:
    """Build a response like ``client.aio.models.generate_content``
    returns."""
    response = MagicMock()
    response.usage_metadata = usage
    candidate = MagicMock()
    candidate.content.parts = [_make_inline_data_part(c) for c in chunks]
    response.candidates = [candidate]
    return response


def _parse_wav_payload(wav_bytes: bytes) -> bytes:
    """Decode a full WAV file and return its raw PCM frames."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        return wav.readframes(wav.getnframes())


class TestGeminiTTSModel(IsolatedAsyncioTestCase):
    """The unittests for the Gemini TTS model (non-realtime)."""

    def setUp(self) -> None:
        """Set up the test case."""
        try:
            import google.genai  # noqa: F401  pylint: disable=unused-import
        except ImportError:
            self.skipTest("google-genai is not installed")

        self.mock_client = MagicMock()
        self.mock_client.aio.models.generate_content = AsyncMock()
        self.patcher = patch(
            "google.genai.Client",
            return_value=self.mock_client,
        )
        self.patcher.start()

    def tearDown(self) -> None:
        """Tear down the test case."""
        self.patcher.stop()

    def _make_model(self) -> GeminiTTSModel:
        """Create a GeminiTTSModel with test credentials."""
        return GeminiTTSModel(
            credential=GeminiCredential(api_key="test"),
            model="gemini-2.5-flash-preview-tts",
            parameters=GeminiTTSModel.Parameters(voice="Kore"),
        )

    async def test_synthesizes_audio(self) -> None:
        """The audio parts are concatenated into a self-contained WAV."""
        self.mock_client.aio.models.generate_content.return_value = (
            _make_api_response(
                [b"AAAA", b"BBBB", b"CCCC"],
                usage=_make_usage_metadata(
                    prompt_token_count=5,
                    candidates_token_count=10,
                ),
            )
        )
        model = self._make_model()

        result = await model.synthesize("Hello world")

        self.assertIsInstance(result, TTSResponse)
        self.assertEqual(result.content.source.media_type, _MEDIA_TYPE)
        wav_bytes = base64.b64decode(result.content.source.data)
        self.assertEqual(_parse_wav_payload(wav_bytes), b"AAAABBBBCCCC")
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            self.assertEqual(wav.getframerate(), _TTS_SAMPLE_RATE)
            self.assertEqual(wav.getnchannels(), _TTS_CHANNELS)
            self.assertEqual(wav.getsampwidth(), _TTS_SAMPLE_WIDTH)
        self.assertTrue(result.is_last)
        self.assertIsNotNone(result.usage)
        self.assertEqual(result.usage.input_tokens, 5)
        self.assertEqual(result.usage.output_tokens, 10)

    async def test_none_short_circuits(self) -> None:
        """``synthesize(None)`` returns an empty response without touching
        the API."""
        model = self._make_model()

        result = await model.synthesize(None)

        self.assertIsNone(result.content)
        self.mock_client.aio.models.generate_content.assert_not_called()

    async def test_empty_string_short_circuits(self) -> None:
        """``synthesize("")`` returns an empty response without touching
        the API."""
        model = self._make_model()

        result = await model.synthesize("")

        self.assertIsNone(result.content)
        self.mock_client.aio.models.generate_content.assert_not_called()

    async def test_empty_response_returns_empty_content(self) -> None:
        """A response with no audio parts yields an empty TTSResponse."""
        self.mock_client.aio.models.generate_content.return_value = (
            _make_api_response([None, None])
        )
        model = self._make_model()

        result = await model.synthesize("Hello world")

        self.assertIsNone(result.content)

    async def test_voice_config_passed_to_api(self) -> None:
        """The configured voice is forwarded to the API call's config."""
        self.mock_client.aio.models.generate_content.return_value = (
            _make_api_response([b"AAAA"])
        )
        model = GeminiTTSModel(
            credential=GeminiCredential(api_key="test"),
            model="gemini-2.5-pro-preview-tts",
            parameters=GeminiTTSModel.Parameters(voice="Puck"),
        )

        await model.synthesize("Hello world")

        _, call_kwargs = self.mock_client.aio.models.generate_content.call_args
        config = call_kwargs["config"]
        self.assertEqual(config["response_modalities"], ["AUDIO"])
        voice_config = config["speech_config"]["voice_config"]
        self.assertEqual(
            voice_config["prebuilt_voice_config"]["voice_name"],
            "Puck",
        )
        self.assertEqual(call_kwargs["model"], "gemini-2.5-pro-preview-tts")
