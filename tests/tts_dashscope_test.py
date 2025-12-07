# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""The unittests for DashScope TTS models."""
import base64
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch, AsyncMock, MagicMock

from agentscope.message import Msg, AudioBlock, Base64Source
from agentscope.tts import (
    DashScopeRealtimeTTSModel,
    DashScopeTTSModel,
    TTSResponse,
)


class DashScopeRealtimeTTSModelTest(IsolatedAsyncioTestCase):
    """The unittests for DashScope Realtime TTS model."""

    def setUp(self) -> None:
        """Set up the test case."""
        self.api_key = "test_api_key"
        self.mock_audio_data = base64.b64encode(b"fake_audio_data").decode(
            "utf-8",
        )

    def _create_mock_tts_client(self) -> Mock:
        """Create a mock QwenTtsRealtime client."""
        mock_client = Mock()
        mock_client.connect = Mock()
        mock_client.close = Mock()
        mock_client.finish = Mock()
        mock_client.update_session = Mock()
        mock_client.append_text = Mock()
        return mock_client

    def _create_mock_dashscope_modules(self) -> dict:
        """Create mock dashscope modules for patching."""
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

    def test_init(self) -> None:
        """Test initialization of DashScopeRealtimeTTSModel."""
        mock_modules = self._create_mock_dashscope_modules()
        mock_tts_client = self._create_mock_tts_client()
        mock_tts_class = Mock(return_value=mock_tts_client)
        mock_modules[
            "dashscope.audio.qwen_tts_realtime"
        ].QwenTtsRealtime = mock_tts_class

        with patch.dict("sys.modules", mock_modules):
            model = DashScopeRealtimeTTSModel(
                api_key=self.api_key,
                stream=False,
            )
            self.assertEqual(model.model_name, "qwen3-tts-flash-realtime")
            self.assertFalse(model.stream)
            self.assertFalse(model._connected)

    async def test_push_incremental_text(self) -> None:
        """Test push method with incremental text chunks."""
        mock_modules = self._create_mock_dashscope_modules()
        mock_client = self._create_mock_tts_client()
        mock_tts_class = Mock(return_value=mock_client)
        mock_modules[
            "dashscope.audio.qwen_tts_realtime"
        ].QwenTtsRealtime = mock_tts_class

        with patch.dict("sys.modules", mock_modules):
            async with DashScopeRealtimeTTSModel(
                api_key=self.api_key,
                stream=False,
            ) as model:
                # Mock callback to return audio data
                model._dashscope_callback.get_audio_data = AsyncMock(
                    return_value=TTSResponse(
                        content=AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                data=self.mock_audio_data,
                                media_type="audio/pcm;rate=24000",
                            ),
                        ),
                    ),
                )

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

                # Verify append_text was called
                self.assertGreater(mock_client.append_text.call_count, 0)

    async def test_synthesize_non_streaming(self) -> None:
        """Test synthesize method in non-streaming mode."""
        mock_modules = self._create_mock_dashscope_modules()
        mock_client = self._create_mock_tts_client()
        mock_tts_class = Mock(return_value=mock_client)
        mock_modules[
            "dashscope.audio.qwen_tts_realtime"
        ].QwenTtsRealtime = mock_tts_class

        with patch.dict("sys.modules", mock_modules):
            async with DashScopeRealtimeTTSModel(
                api_key=self.api_key,
                stream=False,
            ) as model:
                model._dashscope_callback.get_audio_data = AsyncMock(
                    return_value=TTSResponse(
                        content=AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                data=self.mock_audio_data,
                                media_type="audio/pcm;rate=24000",
                            ),
                        ),
                    ),
                )

                msg = Msg(
                    name="user",
                    content="Hello! Test message.",
                    role="user",
                )
                response = await model.synthesize(msg)

                self.assertIsInstance(response, TTSResponse)
                self.assertEqual(response.content["type"], "audio")

    async def test_synthesize_streaming(self) -> None:
        """Test synthesize method in streaming mode."""
        mock_modules = self._create_mock_dashscope_modules()
        mock_client = self._create_mock_tts_client()
        mock_tts_class = Mock(return_value=mock_client)
        mock_modules[
            "dashscope.audio.qwen_tts_realtime"
        ].QwenTtsRealtime = mock_tts_class

        with patch.dict("sys.modules", mock_modules):
            async with DashScopeRealtimeTTSModel(
                api_key=self.api_key,
                stream=True,
            ) as model:

                async def mock_generator() -> AsyncGenerator[
                    TTSResponse,
                    None,
                ]:
                    yield TTSResponse(
                        content=AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                data=self.mock_audio_data,
                                media_type="audio/pcm;rate=24000",
                            ),
                        ),
                    )
                    yield TTSResponse(content=None)

                model._dashscope_callback.get_audio_chunk = mock_generator

                msg = Msg(name="user", content="Test streaming.", role="user")
                response = await model.synthesize(msg)

                self.assertIsInstance(response, AsyncGenerator)
                chunk_count = 0
                async for chunk in response:
                    self.assertIsInstance(chunk, TTSResponse)
                    chunk_count += 1
                self.assertGreater(chunk_count, 0)


class DashScopeTTSModelTest(IsolatedAsyncioTestCase):
    """The unittests for DashScope TTS model (non-realtime)."""

    def setUp(self) -> None:
        """Set up the test case."""
        self.api_key = "test_api_key"
        self.mock_audio_data = "ZmFrZV9hdWRpb19kYXRh"  # base64 encoded

    def _create_mock_response_chunk(self, audio_data: str) -> Mock:
        """Create a mock response chunk."""
        mock_chunk = Mock()
        mock_chunk.output = Mock()
        mock_chunk.output.audio = Mock()
        mock_chunk.output.audio.data = audio_data
        return mock_chunk

    def test_init(self) -> None:
        """Test initialization of DashScopeTTSModel."""
        model = DashScopeTTSModel(
            api_key=self.api_key,
            model_name="qwen3-tts-flash",
            voice="Cherry",
            stream=False,
        )
        self.assertEqual(model.model_name, "qwen3-tts-flash")
        self.assertEqual(model.voice, "Cherry")
        self.assertFalse(model.stream)
        self.assertFalse(model.supports_streaming_input)

    async def test_synthesize_non_streaming(self) -> None:
        """Test synthesize method in non-streaming mode."""
        model = DashScopeTTSModel(
            api_key=self.api_key,
            stream=False,
        )

        mock_chunks = [
            self._create_mock_response_chunk("audio1"),
            self._create_mock_response_chunk("audio2"),
        ]

        with patch("dashscope.MultiModalConversation.call") as mock_call:
            mock_call.return_value = iter(mock_chunks)

            msg = Msg(name="user", content="Hello! Test message.", role="user")
            response = await model.synthesize(msg)

            expected_content = AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data="audio1audio2",
                    media_type="audio/pcm;rate=24000",
                ),
            )
            self.assertEqual(response.content, expected_content)

    async def test_synthesize_streaming(self) -> None:
        """Test synthesize method in streaming mode."""
        model = DashScopeTTSModel(
            api_key=self.api_key,
            stream=True,
        )

        mock_chunks = [
            self._create_mock_response_chunk("audio1"),
            self._create_mock_response_chunk("audio2"),
        ]

        with patch("dashscope.MultiModalConversation.call") as mock_call:
            mock_call.return_value = iter(mock_chunks)

            msg = Msg(name="user", content="Test streaming.", role="user")
            response = await model.synthesize(msg)

            self.assertIsInstance(response, AsyncGenerator)
            chunks = [chunk async for chunk in response]

            # Should have 3 chunks: 2 from audio data + 1 final
            self.assertEqual(len(chunks), 3)

            # Chunk 1: accumulated "audio1"
            self.assertEqual(
                chunks[0].content,
                AudioBlock(
                    type="audio",
                    source=Base64Source(
                        type="base64",
                        data="audio1",
                        media_type="audio/pcm;rate=24000",
                    ),
                ),
            )

            # Chunk 2: accumulated "audio1audio2"
            self.assertEqual(
                chunks[1].content,
                AudioBlock(
                    type="audio",
                    source=Base64Source(
                        type="base64",
                        data="audio1audio2",
                        media_type="audio/pcm;rate=24000",
                    ),
                ),
            )

            # Final chunk: complete audio data
            self.assertEqual(
                chunks[2].content,
                AudioBlock(
                    type="audio",
                    source=Base64Source(
                        type="base64",
                        data="audio1audio2",
                        media_type="audio/pcm;rate=24000",
                    ),
                ),
            )
            self.assertTrue(chunks[2].is_last)
