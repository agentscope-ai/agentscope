# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""The unittests for OpenAI TTS model."""
import base64
import sys
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch, AsyncMock, MagicMock

from agentscope.message import Msg, AudioBlock, Base64Source
from agentscope.tts import OpenAITTSModel


# Create mock openai module (required for import-time patching)
mock_openai = MagicMock()
mock_openai.AsyncOpenAI = Mock(return_value=MagicMock())


@patch.dict(sys.modules, {"openai": mock_openai})
class OpenAITTSModelTest(IsolatedAsyncioTestCase):
    """The unittests for OpenAI TTS model."""

    def setUp(self) -> None:
        """Set up the test case."""
        self.api_key = "test_api_key"
        self.mock_audio_bytes = b"fake_audio_data"
        self.mock_audio_base64 = base64.b64encode(
            self.mock_audio_bytes,
        ).decode(
            "utf-8",
        )

    def test_init(self) -> None:
        """Test initialization of OpenAITTSModel."""
        model = OpenAITTSModel(
            api_key=self.api_key,
            model_name="gpt-4o-mini-tts",
            voice="alloy",
            stream=False,
        )
        self.assertEqual(model.model_name, "gpt-4o-mini-tts")
        self.assertEqual(model.voice, "alloy")
        self.assertFalse(model.stream)
        self.assertFalse(model.supports_streaming_input)

    async def test_synthesize_non_streaming(self) -> None:
        """Test synthesize method in non-streaming mode."""
        model = OpenAITTSModel(
            api_key=self.api_key,
            stream=False,
        )

        # Mock the speech.create response
        mock_response = Mock()
        mock_response.content = self.mock_audio_bytes
        model._client.audio.speech.create = AsyncMock(
            return_value=mock_response,
        )

        msg = Msg(name="user", content="Hello! Test message.", role="user")
        response = await model.synthesize(msg)

        expected_content = AudioBlock(
            type="audio",
            source=Base64Source(
                type="base64",
                data=self.mock_audio_base64,
                media_type="audio/pcm",
            ),
        )
        self.assertEqual(response.content, expected_content)
        model._client.audio.speech.create.assert_called_once()

    async def test_synthesize_streaming(self) -> None:
        """Test synthesize method in streaming mode."""
        model = OpenAITTSModel(
            api_key=self.api_key,
            stream=True,
        )

        chunk1 = b"audio_chunk_1"
        chunk2 = b"audio_chunk_2"

        # Create mock streaming response inline
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)

        async def mock_iter_bytes() -> AsyncGenerator[bytes, None]:
            yield chunk1
            yield chunk2

        mock_stream.iter_bytes = mock_iter_bytes

        model._client.audio.speech.with_streaming_response.create = Mock(
            return_value=mock_stream,
        )

        msg = Msg(name="user", content="Test streaming.", role="user")
        response = await model.synthesize(msg)

        self.assertIsInstance(response, AsyncGenerator)
        chunks = [chunk async for chunk in response]

        # Should have 3 chunks: 2 from audio data + 1 final
        self.assertEqual(len(chunks), 3)

        # Chunk 1
        self.assertEqual(
            chunks[0].content,
            AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data=base64.b64encode(chunk1).decode("utf-8"),
                    media_type="audio/pcm",
                ),
            ),
        )

        # Chunk 2
        self.assertEqual(
            chunks[1].content,
            AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data=base64.b64encode(chunk2).decode("utf-8"),
                    media_type="audio/pcm",
                ),
            ),
        )

        # Final chunk
        self.assertTrue(chunks[2].is_last)
