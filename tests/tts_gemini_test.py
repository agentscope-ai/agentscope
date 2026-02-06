# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""The unittests for Gemini TTS model."""
import base64
import sys
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch, MagicMock

from agentscope.message import Msg, AudioBlock, Base64Source
from agentscope.tts import GeminiTTSModel


# Create mock google.genai modules (required for import-time patching)
mock_types = MagicMock()
mock_types.GenerateContentConfig = Mock(return_value=Mock())
mock_types.SpeechConfig = Mock(return_value=Mock())
mock_types.VoiceConfig = Mock(return_value=Mock())
mock_types.PrebuiltVoiceConfig = Mock(return_value=Mock())

mock_genai = MagicMock()
mock_genai.Client = Mock(return_value=MagicMock())
mock_genai.types = mock_types

mock_google = MagicMock()
mock_google.genai = mock_genai


@patch.dict(
    sys.modules,
    {
        "google": mock_google,
        "google.genai": mock_genai,
        "google.genai.types": mock_types,
    },
)
class GeminiTTSModelTest(IsolatedAsyncioTestCase):
    """The unittests for Gemini TTS model."""

    def setUp(self) -> None:
        """Set up the test case."""
        self.api_key = "test_api_key"
        self.mock_audio_bytes = b"fake_audio_data"
        self.mock_audio_base64 = base64.b64encode(
            self.mock_audio_bytes,
        ).decode(
            "utf-8",
        )
        self.mock_mime_type = "audio/pcm;rate=24000"

    def _create_mock_response(
        self,
        audio_data: bytes,
        mime_type: str,
    ) -> MagicMock:
        """Create a mock Gemini response."""
        mock = MagicMock()
        mock.candidates[0].content.parts[0].inline_data.data = audio_data
        mock.candidates[0].content.parts[0].inline_data.mime_type = mime_type
        return mock

    def test_init(self) -> None:
        """Test initialization of GeminiTTSModel."""
        model = GeminiTTSModel(
            api_key=self.api_key,
            model_name="gemini-2.5-flash-preview-tts",
            voice="Kore",
            stream=False,
        )
        self.assertEqual(model.model_name, "gemini-2.5-flash-preview-tts")
        self.assertEqual(model.voice, "Kore")
        self.assertFalse(model.stream)
        self.assertFalse(model.supports_streaming_input)

    async def test_synthesize_non_streaming(self) -> None:
        """Test synthesize method in non-streaming mode."""
        model = GeminiTTSModel(
            api_key=self.api_key,
            stream=False,
        )

        # Mock the generate_content response
        mock_response = self._create_mock_response(
            self.mock_audio_bytes,
            self.mock_mime_type,
        )
        model._client.models.generate_content = Mock(
            return_value=mock_response,
        )

        msg = Msg(name="user", content="Hello! Test message.", role="user")
        response = await model.synthesize(msg)

        expected_content = AudioBlock(
            type="audio",
            source=Base64Source(
                type="base64",
                data=self.mock_audio_base64,
                media_type=self.mock_mime_type,
            ),
        )
        self.assertEqual(response.content, expected_content)

    async def test_synthesize_streaming(self) -> None:
        """Test synthesize method in streaming mode."""
        model = GeminiTTSModel(
            api_key=self.api_key,
            stream=True,
        )

        # Create mock streaming response chunks
        chunk1_data = b"audio_chunk_1"
        chunk2_data = b"audio_chunk_2"
        mock_chunk1 = self._create_mock_response(
            chunk1_data,
            self.mock_mime_type,
        )
        mock_chunk2 = self._create_mock_response(
            chunk2_data,
            self.mock_mime_type,
        )

        # Mock streaming response
        model._client.models.generate_content_stream = Mock(
            return_value=iter([mock_chunk1, mock_chunk2]),
        )

        msg = Msg(name="user", content="Test streaming.", role="user")
        response = await model.synthesize(msg)

        self.assertIsInstance(response, AsyncGenerator)
        chunks = [chunk async for chunk in response]

        # Should have 3 chunks: 2 from audio data + 1 final empty
        self.assertEqual(len(chunks), 3)

        chunk1_base64 = base64.b64encode(chunk1_data).decode("utf-8")
        chunk2_base64 = base64.b64encode(chunk2_data).decode("utf-8")

        # Chunk 1: accumulated chunk1
        self.assertEqual(
            chunks[0].content,
            AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data=chunk1_base64,
                    media_type=self.mock_mime_type,
                ),
            ),
        )

        # Chunk 2: accumulated chunk1 + chunk2
        self.assertEqual(
            chunks[1].content,
            AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data=chunk1_base64 + chunk2_base64,
                    media_type=self.mock_mime_type,
                ),
            ),
        )

        # Final chunk: empty
        self.assertIsNone(chunks[2].content)
