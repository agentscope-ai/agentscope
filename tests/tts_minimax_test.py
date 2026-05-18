# -*- coding: utf-8 -*-
"""The unittests for MiniMax TTS model."""
import base64
import json
import sys
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch, AsyncMock, MagicMock

from agentscope.message import Msg, AudioBlock, Base64Source
from agentscope.tts import MiniMaxTTSModel


mock_httpx = MagicMock()
mock_httpx.AsyncClient = Mock(return_value=MagicMock())
mock_httpx.Timeout = Mock(return_value=MagicMock())


@patch.dict(sys.modules, {"httpx": mock_httpx})
class MiniMaxTTSModelTest(IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.api_key = "test_api_key"
        self.mock_audio_bytes = b"fake_audio_data"
        self.mock_audio_hex = self.mock_audio_bytes.hex()
        self.mock_audio_base64 = base64.b64encode(
            self.mock_audio_bytes,
        ).decode("utf-8")

    def test_init(self) -> None:
        model = MiniMaxTTSModel(
            api_key=self.api_key,
            model_name="speech-2.8-hd",
            voice_id="English_Graceful_Lady",
            stream=False,
        )
        self.assertEqual(model.model_name, "speech-2.8-hd")
        self.assertEqual(model.voice_id, "English_Graceful_Lady")
        self.assertFalse(model.stream)
        self.assertFalse(model.supports_streaming_input)
        self.assertEqual(
            model.api_url,
            "https://api.minimax.io/v1/t2a_v2",
        )

    def test_init_custom_params(self) -> None:
        model = MiniMaxTTSModel(
            api_key=self.api_key,
            model_name="speech-2.8-turbo",
            voice_id="moss_audio_123",
            stream=True,
            api_url="https://api-uw.minimax.io/v1/t2a_v2",
            voice_setting={"speed": 1.2, "emotion": "happy"},
            audio_setting={"sample_rate": 24000, "format": "pcm"},
        )
        self.assertEqual(model.model_name, "speech-2.8-turbo")
        self.assertEqual(model.voice_id, "moss_audio_123")
        self.assertEqual(
            model.api_url,
            "https://api-uw.minimax.io/v1/t2a_v2",
        )
        self.assertEqual(model.voice_setting["speed"], 1.2)
        self.assertEqual(model.audio_setting["format"], "pcm")

    async def test_synthesize_none_msg(self) -> None:
        model = MiniMaxTTSModel(
            api_key=self.api_key,
            stream=False,
        )
        response = await model.synthesize(None)
        self.assertIsNone(response.content)

    async def test_synthesize_empty_text(self) -> None:
        model = MiniMaxTTSModel(
            api_key=self.api_key,
            stream=False,
        )
        msg = Msg(name="user", content=[], role="user")
        response = await model.synthesize(msg)
        self.assertIsNone(response.content)

    async def test_synthesize_non_streaming(self) -> None:
        model = MiniMaxTTSModel(
            api_key=self.api_key,
            stream=False,
        )

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "base_resp": {"status_code": 0, "status_msg": "success"},
            "data": {
                "audio": self.mock_audio_hex,
                "status": 2,
            },
        }
        model._client.post = AsyncMock(return_value=mock_response)

        msg = Msg(name="user", content="Hello world", role="user")
        response = await model.synthesize(msg)

        expected_content = AudioBlock(
            type="audio",
            source=Base64Source(
                type="base64",
                data=self.mock_audio_base64,
                media_type="audio/mp3",
            ),
        )
        self.assertEqual(response.content, expected_content)
        model._client.post.assert_called_once()

        call_kwargs = model._client.post.call_args
        payload = call_kwargs[1]["json"]
        self.assertEqual(payload["model"], "speech-2.8-hd")
        self.assertEqual(payload["text"], "Hello world")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["voice_setting"]["voice_id"], "English_Graceful_Lady")

    async def test_synthesize_api_error(self) -> None:
        model = MiniMaxTTSModel(
            api_key=self.api_key,
            stream=False,
        )

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "base_resp": {
                "status_code": 1001,
                "status_msg": "invalid parameter",
            },
        }
        model._client.post = AsyncMock(return_value=mock_response)

        msg = Msg(name="user", content="Hello world", role="user")
        with self.assertRaises(RuntimeError) as ctx:
            await model.synthesize(msg)
        self.assertIn("invalid parameter", str(ctx.exception))

    async def test_synthesize_streaming(self) -> None:
        model = MiniMaxTTSModel(
            api_key=self.api_key,
            stream=True,
        )

        audio_bytes_1 = b"chunk_one"
        audio_bytes_2 = b"chunk_two"

        chunk1_json = json.dumps({
            "base_resp": {"status_code": 0},
            "data": {"audio": audio_bytes_1.hex(), "status": 1},
        })
        chunk2_json = json.dumps({
            "base_resp": {"status_code": 0},
            "data": {"audio": audio_bytes_2.hex(), "status": 2},
        })

        class MockStreamResponse:
            def __init__(self) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def raise_for_status(self):
                pass

            async def aiter_text(self):
                yield chunk1_json + "\n"
                yield chunk2_json + "\n"

        model._client.stream = Mock(return_value=MockStreamResponse())

        msg = Msg(name="user", content="Hello streaming", role="user")
        result = await model.synthesize(msg)

        self.assertIsInstance(result, AsyncGenerator)

        chunks = [chunk async for chunk in result]
        self.assertEqual(len(chunks), 2)

        self.assertEqual(
            chunks[0].content,
            AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data=base64.b64encode(audio_bytes_1).decode("utf-8"),
                    media_type="audio/mp3",
                ),
            ),
        )
        self.assertFalse(chunks[0]["is_last"])

        self.assertEqual(
            chunks[1].content,
            AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data=base64.b64encode(audio_bytes_2).decode("utf-8"),
                    media_type="audio/mp3",
                ),
            ),
        )
        self.assertTrue(chunks[1]["is_last"])

    async def test_synthesize_with_custom_audio_format(self) -> None:
        model = MiniMaxTTSModel(
            api_key=self.api_key,
            stream=False,
            audio_setting={"format": "pcm", "sample_rate": 24000},
        )

        mock_response = Mock()
        mock_response.raise_for_status = Mock()
        mock_response.json.return_value = {
            "base_resp": {"status_code": 0},
            "data": {"audio": self.mock_audio_hex, "status": 2},
        }
        model._client.post = AsyncMock(return_value=mock_response)

        msg = Msg(name="user", content="PCM test", role="user")
        response = await model.synthesize(msg)

        self.assertEqual(
            response.content["source"]["media_type"],
            "audio/pcm",
        )
