# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""The unittests for DashScope CosyVoice TTS models."""
import base64
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch, AsyncMock, MagicMock

from agentscope.message import Msg, AudioBlock, Base64Source
from agentscope.tts import (
    DashScopeCosyVoiceRealtimeTTSModel,
    DashScopeCosyVoiceTTSModel,
    TTSResponse,
)


class DashScopeCosyVoiceTTSModelTest(IsolatedAsyncioTestCase):
    """The unittests for DashScope CosyVoice TTS model (non-realtime)."""

    def setUp(self) -> None:
        """Set up the test case."""
        self.api_key = "test_api_key"
        self.mock_audio_bytes = b"fake_audio_data_bytes"
        self.mock_audio_base64 = base64.b64encode(
            self.mock_audio_bytes,
        ).decode("utf-8")

    def _create_mock_dashscope_modules(self) -> dict:
        """Create mock dashscope modules for patching."""
        mock_audio_format = MagicMock()
        mock_audio_format.PCM_24000HZ_MONO_16BIT = "pcm_24000hz_mono_16bit"

        mock_speech_synthesizer = MagicMock()

        mock_tts_v2 = MagicMock()
        mock_tts_v2.SpeechSynthesizer = mock_speech_synthesizer
        mock_tts_v2.AudioFormat = mock_audio_format
        mock_tts_v2.ResultCallback = Mock

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

    def test_init(self) -> None:
        """Test initialization of DashScopeCosyVoiceTTSModel."""
        mock_modules = self._create_mock_dashscope_modules()

        with patch.dict("sys.modules", mock_modules):
            model = DashScopeCosyVoiceTTSModel(
                api_key=self.api_key,
                model_name="cosyvoice-v3-plus",
                voice="longanyang",
                stream=False,
            )
            self.assertEqual(model.model_name, "cosyvoice-v3-plus")
            self.assertEqual(model.voice, "longanyang")
            self.assertFalse(model.stream)
            self.assertFalse(model.supports_streaming_input)

    async def test_synthesize_non_streaming(self) -> None:
        """Test synthesize method in non-streaming mode."""
        mock_modules = self._create_mock_dashscope_modules()

        with patch.dict("sys.modules", mock_modules):
            model = DashScopeCosyVoiceTTSModel(
                api_key=self.api_key,
                stream=False,
            )

            # Mock _create_synthesizer to return a mock synthesizer
            mock_synthesizer = MagicMock()
            mock_synthesizer.call.return_value = self.mock_audio_bytes
            model._create_synthesizer = Mock(
                return_value=(mock_synthesizer, None),
            )

            msg = Msg(name="user", content="Hello! Test message.", role="user")
            response = await model.synthesize(msg)

            self.assertIsInstance(response, TTSResponse)
            self.assertEqual(response.content["type"], "audio")
            self.assertEqual(
                response.content["source"]["data"],
                self.mock_audio_base64,
            )
            mock_synthesizer.call.assert_called_once_with(
                text="Hello! Test message.",
            )

    async def test_synthesize_streaming(self) -> None:
        """Test synthesize method in streaming mode."""
        mock_modules = self._create_mock_dashscope_modules()

        with patch.dict("sys.modules", mock_modules):
            model = DashScopeCosyVoiceTTSModel(
                api_key=self.api_key,
                stream=True,
            )

            # Create mock callback with proper async generator
            mock_callback = MagicMock()

            async def mock_generator() -> AsyncGenerator[TTSResponse, None]:
                yield TTSResponse(
                    content=AudioBlock(
                        type="audio",
                        source=Base64Source(
                            type="base64",
                            data=self.mock_audio_base64,
                            media_type="audio/pcm;rate=24000",
                        ),
                    ),
                    is_last=False,
                )
                yield TTSResponse(
                    content=AudioBlock(
                        type="audio",
                        source=Base64Source(
                            type="base64",
                            data=self.mock_audio_base64,
                            media_type="audio/pcm;rate=24000",
                        ),
                    ),
                    is_last=True,
                )

            # Directly return the generator object
            mock_callback.get_audio_chunk = Mock(return_value=mock_generator())

            mock_synthesizer = MagicMock()
            model._create_synthesizer = Mock(
                return_value=(mock_synthesizer, mock_callback),
            )

            msg = Msg(name="user", content="Test streaming.", role="user")
            response = await model.synthesize(msg)

            # Verify response is an async generator
            self.assertIsInstance(response, AsyncGenerator)
            chunks = [chunk async for chunk in response]

            # Verify we got some chunks
            self.assertGreater(len(chunks), 0)
            # Verify each chunk is a TTSResponse
            for chunk in chunks:
                self.assertIsInstance(chunk, TTSResponse)
            mock_synthesizer.call.assert_called_once_with(
                text="Test streaming.",
            )


class DashScopeCosyVoiceRealtimeTTSModelTest(IsolatedAsyncioTestCase):
    """The unittests for DashScope CosyVoice Realtime TTS model."""

    def setUp(self) -> None:
        """Set up the test case."""
        self.api_key = "test_api_key"
        self.mock_audio_bytes = b"fake_audio_data_bytes"
        self.mock_audio_base64 = base64.b64encode(
            self.mock_audio_bytes,
        ).decode("utf-8")

    def _create_mock_dashscope_modules(self) -> dict:
        """Create mock dashscope modules for patching."""
        mock_audio_format = MagicMock()
        mock_audio_format.PCM_24000HZ_MONO_16BIT = "pcm_24000hz_mono_16bit"

        mock_speech_synthesizer = MagicMock()

        mock_tts_v2 = MagicMock()
        mock_tts_v2.SpeechSynthesizer = mock_speech_synthesizer
        mock_tts_v2.AudioFormat = mock_audio_format
        mock_tts_v2.ResultCallback = Mock

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

    def _create_mock_callback(self) -> MagicMock:
        """Create a mock callback for testing."""
        mock_callback = MagicMock()
        mock_callback.chunk_event = MagicMock()
        mock_callback.finish_event = MagicMock()
        mock_callback._audio_bytes = b""
        mock_callback._audio_base64 = ""
        mock_callback._last_encoded_pos = 0
        return mock_callback

    def test_init(self) -> None:
        """Test initialization of DashScopeCosyVoiceRealtimeTTSModel."""
        mock_modules = self._create_mock_dashscope_modules()

        with patch.dict("sys.modules", mock_modules):
            # Mock _get_cosyvoice_callback_class
            with patch(
                "agentscope.tts._dashscope_cosyvoice_realtime_tts_model"
                "._get_cosyvoice_callback_class",
            ) as mock_get_callback:
                mock_callback_class = MagicMock()
                mock_callback_class.return_value = self._create_mock_callback()
                mock_get_callback.return_value = mock_callback_class

                model = DashScopeCosyVoiceRealtimeTTSModel(
                    api_key=self.api_key,
                    model_name="cosyvoice-v3-plus",
                    voice="longanyang",
                    stream=True,
                )
                self.assertEqual(model.model_name, "cosyvoice-v3-plus")
                self.assertEqual(model.voice, "longanyang")
                self.assertTrue(model.stream)
                self.assertTrue(model.supports_streaming_input)

    async def test_push_incremental_text(self) -> None:
        """Test push method with incremental text chunks."""
        mock_modules = self._create_mock_dashscope_modules()
        mock_synthesizer_instance = MagicMock()
        mock_modules[
            "dashscope.audio.tts_v2"
        ].SpeechSynthesizer.return_value = mock_synthesizer_instance

        with patch.dict("sys.modules", mock_modules):
            with patch(
                "agentscope.tts._dashscope_cosyvoice_realtime_tts_model"
                "._get_cosyvoice_callback_class",
            ) as mock_get_callback:
                mock_callback_class = MagicMock()
                mock_callback = self._create_mock_callback()
                mock_callback.get_audio_data = AsyncMock(
                    return_value=TTSResponse(
                        content=AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                data=self.mock_audio_base64,
                                media_type="audio/pcm;rate=24000",
                            ),
                        ),
                    ),
                )
                mock_callback_class.return_value = mock_callback
                mock_get_callback.return_value = mock_callback_class

                model = DashScopeCosyVoiceRealtimeTTSModel(
                    api_key=self.api_key,
                )
                await model.connect()

                msg_id = "test_msg_001"
                text_chunks = ["Hello there!\n\n", "This is a test message."]

                accumulated_text = ""
                for chunk in text_chunks:
                    accumulated_text += chunk
                    msg = Msg(
                        name="user",
                        content=accumulated_text,
                        role="user",
                    )
                    msg.id = msg_id

                    response = await model.push(msg)
                    self.assertIsInstance(response, TTSResponse)

                # Verify streaming_call was called
                self.assertGreater(
                    mock_synthesizer_instance.streaming_call.call_count,
                    0,
                )

    async def test_synthesize_non_streaming(self) -> None:
        """Test synthesize method in non-streaming mode."""
        mock_modules = self._create_mock_dashscope_modules()
        mock_synthesizer_instance = MagicMock()
        mock_modules[
            "dashscope.audio.tts_v2"
        ].SpeechSynthesizer.return_value = mock_synthesizer_instance

        with patch.dict("sys.modules", mock_modules):
            with patch(
                "agentscope.tts._dashscope_cosyvoice_realtime_tts_model"
                "._get_cosyvoice_callback_class",
            ) as mock_get_callback:
                mock_callback_class = MagicMock()
                mock_callback = self._create_mock_callback()
                mock_callback.get_audio_data = AsyncMock(
                    return_value=TTSResponse(
                        content=AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                data=self.mock_audio_base64,
                                media_type="audio/pcm;rate=24000",
                            ),
                        ),
                    ),
                )
                mock_callback_class.return_value = mock_callback
                mock_get_callback.return_value = mock_callback_class

                model = DashScopeCosyVoiceRealtimeTTSModel(
                    api_key=self.api_key,
                    stream=False,
                )
                await model.connect()

                msg = Msg(
                    name="user",
                    content="Hello! Test message.",
                    role="user",
                )
                response = await model.synthesize(msg)

                self.assertIsInstance(response, TTSResponse)
                self.assertEqual(response.content["type"], "audio")
                mock_synthesizer_instance.streaming_complete.assert_called_once()  # noqa

    async def test_synthesize_streaming(self) -> None:
        """Test synthesize method in streaming mode."""
        mock_modules = self._create_mock_dashscope_modules()
        mock_synthesizer_instance = MagicMock()
        mock_modules[
            "dashscope.audio.tts_v2"
        ].SpeechSynthesizer.return_value = mock_synthesizer_instance

        with patch.dict("sys.modules", mock_modules):
            with patch(
                "agentscope.tts._dashscope_cosyvoice_realtime_tts_model"
                "._get_cosyvoice_callback_class",
            ) as mock_get_callback:
                mock_callback_class = MagicMock()
                mock_callback = self._create_mock_callback()

                async def mock_generator() -> AsyncGenerator[
                    TTSResponse,
                    None,
                ]:
                    yield TTSResponse(
                        content=AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                data=self.mock_audio_base64,
                                media_type="audio/pcm;rate=24000",
                            ),
                        ),
                        is_last=False,
                    )
                    yield TTSResponse(
                        content=AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                data=self.mock_audio_base64,
                                media_type="audio/pcm;rate=24000",
                            ),
                        ),
                        is_last=True,
                    )

                mock_callback.get_audio_chunk = Mock(
                    return_value=mock_generator(),
                )
                mock_callback_class.return_value = mock_callback
                mock_get_callback.return_value = mock_callback_class

                model = DashScopeCosyVoiceRealtimeTTSModel(
                    api_key=self.api_key,
                    stream=True,
                )
                await model.connect()

                msg = Msg(name="user", content="Test streaming.", role="user")
                response = await model.synthesize(msg)

                # Verify response is an async generator
                self.assertIsInstance(response, AsyncGenerator)
                chunks = [chunk async for chunk in response]

                # Verify we got some chunks
                self.assertGreater(len(chunks), 0)
                # Verify each chunk is a TTSResponse
                for chunk in chunks:
                    self.assertIsInstance(chunk, TTSResponse)
