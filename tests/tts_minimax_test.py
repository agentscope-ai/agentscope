# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the MiniMax TTS model."""
import base64
import json
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from agentscope.credential import CredentialFactory, MiniMaxCredential
from agentscope.tts import MiniMaxTTSModel, TTSResponse


_GLOBAL_API_URL = "https://api.minimax.io/v1/t2a_v2"
_CHINA_API_URL = "https://api.minimaxi.com/v1/t2a_v2"


class _StreamResponse:
    """Mock streaming response with newline-delimited payloads."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    @staticmethod
    def raise_for_status() -> None:
        """Simulate a successful HTTP response."""

    async def aiter_lines(self) -> Any:
        """Yield mock response lines."""
        for line in self.lines:
            yield line


class TestMiniMaxTTSModel(IsolatedAsyncioTestCase):
    """The unit tests for MiniMax TTS synthesis."""

    def setUp(self) -> None:
        """Replace the HTTP client with a mock."""
        self.client = MagicMock()
        self.client_patcher = patch(
            "httpx.AsyncClient",
            return_value=self.client,
        )
        self.client_patcher.start()

    def tearDown(self) -> None:
        """Restore the HTTP client constructor."""
        self.client_patcher.stop()

    @staticmethod
    def _make_model(
        stream: bool = False,
        **kwargs: Any,
    ) -> MiniMaxTTSModel:
        """Create a model with test credentials."""
        return MiniMaxTTSModel(
            credential=MiniMaxCredential(api_key="test"),
            stream=stream,
            **kwargs,
        )

    async def test_non_streaming_synthesis(self) -> None:
        """The non-streaming response contains the decoded audio."""
        audio = b"complete audio"
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {
            "data": {"audio": audio.hex(), "status": 2},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        }
        self.client.post = AsyncMock(return_value=response)
        model = self._make_model(stream=False)

        result = await model.synthesize(
            "Hello world",
            pronunciation_dict={"tone": ["world/wɜːrld"]},
            voice_modify={"pitch": 1},
            subtitle_enable=True,
        )

        self.assertIsInstance(result, TTSResponse)
        self.assertEqual(result.content.source.media_type, "audio/mpeg")
        self.assertEqual(
            base64.b64decode(result.content.source.data),
            audio,
        )
        self.client.post.assert_awaited_once()
        api_url, call_kwargs = self.client.post.call_args
        self.assertEqual(api_url[0], _GLOBAL_API_URL)
        payload = call_kwargs["json"]
        self.assertEqual(payload["model"], "speech-2.8-hd")
        self.assertEqual(payload["text"], "Hello world")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["output_format"], "hex")
        self.assertEqual(
            payload["voice_setting"]["voice_id"],
            "English_expressive_narrator",
        )
        self.assertEqual(payload["audio_setting"]["format"], "mp3")
        self.assertIn("pronunciation_dict", payload)
        self.assertIn("voice_modify", payload)
        self.assertTrue(payload["subtitle_enable"])

    async def test_regional_endpoint_and_audio_format(self) -> None:
        """A regional endpoint and audio format are configurable."""
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {
            "data": {"audio": b"wave".hex(), "status": 2},
            "base_resp": {"status_code": 0},
        }
        self.client.post = AsyncMock(return_value=response)
        parameters = MiniMaxTTSModel.Parameters(
            api_url=_CHINA_API_URL,
            response_format="wav",
        )
        model = self._make_model(stream=False, parameters=parameters)

        result = await model.synthesize("Hello")

        api_url, _ = self.client.post.call_args
        self.assertEqual(api_url[0], _CHINA_API_URL)
        self.assertEqual(result.content.source.media_type, "audio/wav")

    async def test_streaming_synthesis(self) -> None:
        """Streaming accepts SSE and plain JSON response lines."""
        first = b"first"
        second = b"second"
        lines = [
            "data: "
            + json.dumps(
                {
                    "data": {"audio": first.hex(), "status": 1},
                    "base_resp": {"status_code": 0},
                },
            ),
            json.dumps(
                {
                    "data": {"audio": second.hex(), "status": 2},
                    "base_resp": {"status_code": 0},
                },
            ),
            "data: [DONE]",
        ]
        stream_response = _StreamResponse(lines)
        stream_context = MagicMock()
        stream_context.__aenter__ = AsyncMock(return_value=stream_response)
        stream_context.__aexit__ = AsyncMock(return_value=None)
        self.client.stream = Mock(return_value=stream_context)
        model = self._make_model(stream=True)

        generator = await model.synthesize("Hello")
        chunks = [chunk async for chunk in generator]

        self.assertEqual(
            [base64.b64decode(chunk.content.source.data) for chunk in chunks],
            [first, second],
        )
        self.assertEqual([chunk.is_last for chunk in chunks], [False, True])
        _, _, call_kwargs = self.client.stream.mock_calls[0]
        self.assertTrue(call_kwargs["json"]["stream"])

    async def test_api_error(self) -> None:
        """An unsuccessful API status raises a clear error."""
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {
            "base_resp": {
                "status_code": 1001,
                "status_msg": "invalid parameter",
            },
        }
        self.client.post = AsyncMock(return_value=response)
        model = self._make_model(stream=False)

        with self.assertRaisesRegex(RuntimeError, "invalid parameter"):
            await model.synthesize("Hello")

    async def test_empty_input_short_circuits(self) -> None:
        """Empty input returns without sending an API request."""
        model = self._make_model(stream=False)
        self.client.post = AsyncMock()

        result = await model.synthesize("")

        self.assertIsNone(result.content)
        self.client.post.assert_not_awaited()

    async def test_model_cards_and_credential_wiring(self) -> None:
        """All supported models are discoverable from the credential."""
        expected_names = {
            "speech-2.8-hd",
            "speech-2.8-turbo",
            "speech-2.6-hd",
            "speech-2.6-turbo",
            "speech-02-hd",
            "speech-02-turbo",
            "speech-01-hd",
            "speech-01-turbo",
        }

        self.assertIs(
            CredentialFactory.get_credential_class("minimax_credential"),
            MiniMaxCredential,
        )
        self.assertEqual(
            {card.name for card in MiniMaxCredential.list_tts_models()},
            expected_names,
        )
        self.assertEqual(
            MiniMaxCredential.get_tts_model_classes(),
            [MiniMaxTTSModel],
        )
