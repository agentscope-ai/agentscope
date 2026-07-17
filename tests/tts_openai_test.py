# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the OpenAI TTS model.

Covers:
  * ``OpenAITTSModel`` non-streaming aggregation.
  * ``OpenAITTSModel`` streaming: incremental chunks and ``is_last``
    placement at the final chunk only.
  * Edge cases: empty/None input short-circuits without calling the API.
"""
import base64
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.credential import OpenAICredential
from agentscope.tts import OpenAITTSModel, TTSResponse


_MEDIA_TYPE_MP3 = "audio/mpeg"
_MEDIA_TYPE_WAV = "audio/wav"


def _make_mock_client(audio_bytes: bytes, chunks: list[bytes]) -> MagicMock:
    """Build a mock ``openai.AsyncClient`` shaped like what
    ``audio.speech.create`` / ``with_streaming_response.create`` return."""
    client = MagicMock()

    # Non-streaming: client.audio.speech.create(...) -> response with
    # .content bytes.
    create_response = MagicMock()
    create_response.content = audio_bytes
    client.audio.speech.create = AsyncMock(return_value=create_response)

    # Streaming: client.audio.speech.with_streaming_response.create(...)
    # is an async context manager whose value exposes .iter_bytes().
    stream_response = MagicMock()

    async def _iter_bytes() -> Any:
        for chunk in chunks:
            yield chunk

    stream_response.iter_bytes = _iter_bytes

    stream_ctx = MagicMock()
    stream_ctx.__aenter__ = AsyncMock(return_value=stream_response)
    stream_ctx.__aexit__ = AsyncMock(return_value=None)

    client.audio.speech.with_streaming_response.create = MagicMock(
        return_value=stream_ctx,
    )

    return client


class TestOpenAITTSModel(IsolatedAsyncioTestCase):
    """The unittests for the OpenAI TTS model (non-realtime)."""

    def _make_model(self, stream: bool = False, **kwargs: Any) -> Any:
        """Create an OpenAITTSModel with test credentials."""
        return OpenAITTSModel(
            credential=OpenAICredential(api_key="test"),
            model="tts-1",
            stream=stream,
            **kwargs,
        )

    async def test_aggregates_response(self) -> None:
        """Non-streaming returns a single TTSResponse with the full audio."""
        client = _make_mock_client(b"AAAABBBBCCCC", [])
        model = self._make_model(stream=False)

        with patch("openai.AsyncClient", return_value=client):
            result = await model.synthesize("Hello world")

        self.assertIsInstance(result, TTSResponse)
        self.assertEqual(result.content.source.media_type, _MEDIA_TYPE_MP3)
        self.assertEqual(
            base64.b64decode(result.content.source.data),
            b"AAAABBBBCCCC",
        )
        self.assertTrue(result.is_last)

    async def test_none_short_circuits(self) -> None:
        """``synthesize(None)`` returns an empty response without touching
        the API."""
        model = self._make_model(stream=False)

        with patch("openai.AsyncClient") as mock_client_cls:
            result = await model.synthesize(None)

        self.assertIsNone(result.content)
        mock_client_cls.assert_not_called()

    async def test_empty_string_short_circuits(self) -> None:
        """``synthesize("")`` returns an empty response without touching
        the API."""
        model = self._make_model(stream=False)

        with patch("openai.AsyncClient") as mock_client_cls:
            result = await model.synthesize("")

        self.assertIsNone(result.content)
        mock_client_cls.assert_not_called()

    async def test_wav_media_type(self) -> None:
        """The media type follows the ``response_format`` parameter."""
        client = _make_mock_client(b"AAAA", [])
        model = self._make_model(
            stream=False,
            parameters=OpenAITTSModel.Parameters(response_format="wav"),
        )

        with patch("openai.AsyncClient", return_value=client):
            result = await model.synthesize("Hello world")

        self.assertEqual(result.content.source.media_type, _MEDIA_TYPE_WAV)

    async def test_incremental_chunks(self) -> None:
        """Each streamed byte chunk yields one TTSResponse."""
        client = _make_mock_client(b"", [b"AAAA", b"BBBB", b"CCCC"])
        model = self._make_model(stream=True)

        with patch("openai.AsyncClient", return_value=client):
            gen = await model.synthesize("Hello world")
            chunks = [c async for c in gen]

        payloads = [base64.b64decode(c.content.source.data) for c in chunks]
        self.assertEqual(payloads, [b"AAAA", b"BBBB", b"CCCC"])
        self.assertEqual(
            [c.is_last for c in chunks],
            [False, False, True],
        )
        self.assertEqual(
            [c.content.source.media_type for c in chunks],
            [_MEDIA_TYPE_MP3] * 3,
        )

    async def test_single_chunk_marked_last(self) -> None:
        """A lone audio chunk is flagged ``is_last=True``."""
        client = _make_mock_client(b"", [b"ONLYCHUNK"])
        model = self._make_model(stream=True)

        with patch("openai.AsyncClient", return_value=client):
            gen = await model.synthesize("Hello world")
            chunks = [c async for c in gen]

        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].is_last)
        self.assertEqual(
            base64.b64decode(chunks[0].content.source.data),
            b"ONLYCHUNK",
        )

    async def test_empty_stream_yields_terminal(self) -> None:
        """When the API yields no audio, the generator emits a terminal
        sentinel so consumers can detect EOS."""
        client = _make_mock_client(b"", [])
        model = self._make_model(stream=True)

        with patch("openai.AsyncClient", return_value=client):
            gen = await model.synthesize("Hello world")
            chunks = [c async for c in gen]

        self.assertEqual(len(chunks), 1)
        self.assertIsNone(chunks[0].content)
        self.assertTrue(chunks[0].is_last)
