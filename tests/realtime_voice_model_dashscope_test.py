# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for DashScopeRealtimeModel class."""
import json
import unittest
from unittest.mock import patch

from agentscope.agent.realtime_voice_agent import (
    DashScopeRealtimeModel,
)
from agentscope.agent.realtime_voice_agent.events import (
    ModelEventType,
    ModelSessionCreated,
    ModelSessionUpdated,
    ModelResponseCreated,
    ModelResponseDone,
    ModelResponseAudioDelta,
    ModelResponseAudioDone,
    ModelResponseAudioTranscriptDelta,
    ModelResponseAudioTranscriptDone,
    ModelResponseToolUseDelta,
    ModelResponseToolUseDone,
    ModelInputTranscriptionDone,
    ModelInputStarted,
    ModelInputDone,
    ModelError,
)


class TestDashScopeRealtimeModelInit(unittest.TestCase):
    """Test cases for DashScopeRealtimeModel initialization."""

    def test_init_default_params(self) -> None:
        """Test initialization with default parameters."""
        model = DashScopeRealtimeModel(
            api_key="test_key",
        )
        self.assertEqual(model.api_key, "test_key")
        self.assertEqual(model.model_name, "qwen3-omni-flash-realtime")
        self.assertEqual(model.voice, "Cherry")
        self.assertEqual(model.instructions, "You are a helpful assistant.")
        self.assertTrue(model.vad_enabled)
        self.assertTrue(model.enable_input_audio_transcription)
        self.assertEqual(model.input_audio_format, "pcm")
        self.assertEqual(model.input_sample_rate, 16000)
        self.assertEqual(model.output_audio_format, "pcm")
        self.assertEqual(model.output_sample_rate, 24000)
        self.assertEqual(
            model.base_url,
            "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        )

    def test_init_custom_params(self) -> None:
        """Test initialization with custom parameters."""
        model = DashScopeRealtimeModel(
            api_key="custom_key",
            model_name="custom-model",
            voice="Serena",
            instructions="Be concise.",
            vad_enabled=False,
            enable_input_audio_transcription=False,
            input_sample_rate=48000,
            output_sample_rate=16000,
            base_url="wss://custom.example.com/ws",
            generate_kwargs={"temperature": 0.7},
        )
        self.assertEqual(model.api_key, "custom_key")
        self.assertEqual(model.model_name, "custom-model")
        self.assertEqual(model.voice, "Serena")
        self.assertEqual(model.instructions, "Be concise.")
        self.assertFalse(model.vad_enabled)
        self.assertFalse(model.enable_input_audio_transcription)
        self.assertEqual(model.input_sample_rate, 48000)
        self.assertEqual(model.output_sample_rate, 16000)
        self.assertEqual(model.base_url, "wss://custom.example.com/ws")
        self.assertEqual(model.generate_kwargs, {"temperature": 0.7})

    def test_provider_name(self) -> None:
        """Test provider_name property."""
        model = DashScopeRealtimeModel(api_key="test")
        self.assertEqual(model.provider_name, "dashscope")

    def test_supports_image(self) -> None:
        """Test supports_image property."""
        model = DashScopeRealtimeModel(api_key="test")
        self.assertTrue(model.supports_image)


class TestDashScopeRealtimeModelConfig(unittest.TestCase):
    """Test cases for configuration methods."""

    def setUp(self) -> None:
        """Set up test model."""
        self.model = DashScopeRealtimeModel(
            api_key="test_key",
            model_name="test-model",
            voice="Cherry",
            instructions="Test instructions",
        )

    def test_get_websocket_url(self) -> None:
        """Test WebSocket URL generation."""
        url = self.model._get_websocket_url()
        expected = (
            "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=test-model"
        )
        self.assertEqual(url, expected)

    def test_get_websocket_url_custom_base(self) -> None:
        """Test WebSocket URL generation with custom base_url."""
        model = DashScopeRealtimeModel(
            api_key="test_key",
            model_name="test-model",
            base_url="wss://custom.example.com/ws",
        )
        url = model._get_websocket_url()
        self.assertEqual(url, "wss://custom.example.com/ws?model=test-model")

    def test_get_headers(self) -> None:
        """Test authentication headers."""
        headers = self.model._get_headers()
        self.assertEqual(headers["Authorization"], "Bearer test_key")
        self.assertEqual(headers["X-DashScope-DataInspection"], "disable")

    def test_build_session_config_with_vad(self) -> None:
        """Test session config with VAD enabled."""
        self.model.vad_enabled = True
        self.model.enable_input_audio_transcription = True
        config_str = self.model._build_session_config()
        config = json.loads(config_str)

        self.assertEqual(config["type"], "session.update")
        session = config["session"]
        self.assertEqual(session["voice"], "Cherry")
        self.assertEqual(session["instructions"], "Test instructions")
        self.assertEqual(session["turn_detection"], {"type": "server_vad"})
        self.assertIn("audio", session["modalities"])
        self.assertIn("text", session["modalities"])
        self.assertIn("input_audio_transcription", session)

    def test_build_session_config_without_transcription(self) -> None:
        """Test session config with input audio transcription disabled."""
        self.model.enable_input_audio_transcription = False
        config_str = self.model._build_session_config()
        config = json.loads(config_str)

        session = config["session"]
        self.assertNotIn("input_audio_transcription", session)

    def test_build_session_config_without_vad(self) -> None:
        """Test session config with VAD disabled."""
        self.model.vad_enabled = False
        config_str = self.model._build_session_config()
        config = json.loads(config_str)

        session = config["session"]
        self.assertIsNone(session["turn_detection"])

    def test_build_session_config_with_custom_instructions(self) -> None:
        """Test session config with custom instructions override."""
        config_str = self.model._build_session_config(
            instructions="Override instructions",
        )
        config = json.loads(config_str)
        session = config["session"]
        self.assertEqual(session["instructions"], "Override instructions")


class TestDashScopeRealtimeModelFormatMessages(unittest.TestCase):
    """Test cases for message formatting methods."""

    def setUp(self) -> None:
        """Set up test model."""
        self.model = DashScopeRealtimeModel(api_key="test_key")

    def test_format_audio_message(self) -> None:
        """Test audio message formatting."""
        audio_b64 = "SGVsbG8gV29ybGQ="  # "Hello World" in base64
        msg_str = self.model._format_audio_message(audio_b64)
        msg = json.loads(msg_str)

        self.assertEqual(msg["type"], "input_audio_buffer.append")
        self.assertEqual(msg["audio"], audio_b64)

    def test_format_cancel_message(self) -> None:
        """Test cancel message formatting."""
        msg_str = self.model._format_cancel_message()
        msg = json.loads(msg_str)

        self.assertEqual(msg["type"], "response.cancel")

    def test_format_tool_result_message(self) -> None:
        """Test tool result message formatting."""
        msg_str = self.model._format_tool_result_message(
            tool_id="call_123",
            tool_name="get_weather",
            result='{"temp": 22}',
        )
        msg = json.loads(msg_str)

        self.assertEqual(msg["type"], "conversation.item.create")
        self.assertEqual(msg["item"]["type"], "function_call_output")
        self.assertEqual(msg["item"]["call_id"], "call_123")
        self.assertEqual(msg["item"]["output"], '{"temp": 22}')

    def test_format_image_message(self) -> None:
        """Test image message formatting."""
        image_b64 = "/9j/4AAQSkZJRg..."
        msg_str = self.model._format_image_message(image_b64)
        msg = json.loads(msg_str)

        self.assertEqual(msg["type"], "input_image_buffer.append")
        self.assertEqual(msg["image"], image_b64)

    def test_format_image_message_with_non_jpeg(self) -> None:
        """Test image message with non-JPEG mime type logs warning."""
        with patch(
            "agentscope.agent.realtime_voice_agent.model_dashscope.logger",
        ) as mock_logger:
            msg_str = self.model._format_image_message(
                "abc123",
                mime_type="image/png",
            )
            mock_logger.warning.assert_called_once()
            # Should still return message
            msg = json.loads(msg_str)
            self.assertEqual(msg["type"], "input_image_buffer.append")

    def test_format_text_message_returns_none(self) -> None:
        """Test text message returns None (not supported)."""
        with patch(
            "agentscope.agent.realtime_voice_agent.model_dashscope.logger",
        ):
            self.assertIsNone(self.model._format_text_message("Hello"))


class TestDashScopeRealtimeModelSessionUpdate(unittest.TestCase):
    """Test cases for session update message formatting."""

    def setUp(self) -> None:
        """Set up test model."""
        self.model = DashScopeRealtimeModel(api_key="test_key")

    def test_format_session_update_with_voice(self) -> None:
        """Test session update with voice."""
        msg_str = self.model._format_session_update_message(
            {"voice": "Serena"},
        )
        msg = json.loads(msg_str)

        self.assertEqual(msg["type"], "session.update")
        self.assertEqual(msg["session"]["voice"], "Serena")

    def test_format_session_update_with_instructions(self) -> None:
        """Test session update with instructions."""
        msg_str = self.model._format_session_update_message(
            {"instructions": "New instructions"},
        )
        msg = json.loads(msg_str)

        self.assertEqual(msg["session"]["instructions"], "New instructions")

    def test_format_session_update_with_multiple_fields(self) -> None:
        """Test session update with multiple fields."""
        msg_str = self.model._format_session_update_message(
            {
                "voice": "Ethan",
                "instructions": "Be brief",
            },
        )
        msg = json.loads(msg_str)

        self.assertEqual(msg["session"]["voice"], "Ethan")
        self.assertEqual(msg["session"]["instructions"], "Be brief")

    def test_format_session_update_empty_config(self) -> None:
        """Test session update with empty config returns None."""
        result = self.model._format_session_update_message({})
        self.assertIsNone(result)

    def test_format_session_update_unknown_fields_ignored(self) -> None:
        """Test session update ignores unknown fields."""
        msg_str = self.model._format_session_update_message(
            {
                "voice": "Cherry",
                "unknown_field": "value",
                "another_unknown": 123,
            },
        )
        msg = json.loads(msg_str)

        self.assertEqual(msg["session"]["voice"], "Cherry")
        self.assertNotIn("unknown_field", msg["session"])
        self.assertNotIn("another_unknown", msg["session"])


class TestDashScopeRealtimeModelParseServerMessage(unittest.TestCase):
    """Test cases for server message parsing."""

    def setUp(self) -> None:
        """Set up test model."""
        self.model = DashScopeRealtimeModel(api_key="test_key")

    def test_parse_session_created(self) -> None:
        """Test parsing session.created event."""
        msg = json.dumps(
            {
                "type": "session.created",
                "session": {"id": "sess_123"},
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelSessionCreated)
        self.assertEqual(event.session_id, "sess_123")

    def test_parse_session_updated(self) -> None:
        """Test parsing session.updated event."""
        msg = json.dumps(
            {
                "type": "session.updated",
                "session": {"id": "sess_456"},
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelSessionUpdated)
        self.assertEqual(event.session_id, "sess_456")

    def test_parse_response_created(self) -> None:
        """Test parsing response.created event."""
        msg = json.dumps(
            {
                "type": "response.created",
                "response": {"id": "resp_789"},
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelResponseCreated)
        self.assertEqual(event.response_id, "resp_789")
        # Check internal state is updated
        self.assertEqual(self.model._current_response_id, "resp_789")

    def test_parse_response_done(self) -> None:
        """Test parsing response.done event."""
        msg = json.dumps(
            {
                "type": "response.done",
                "response": {
                    "id": "resp_abc",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                    },
                },
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelResponseDone)
        self.assertEqual(event.response_id, "resp_abc")
        self.assertEqual(event.input_tokens, 100)
        self.assertEqual(event.output_tokens, 50)

    def test_parse_audio_delta(self) -> None:
        """Test parsing response.audio.delta event."""
        self.model._current_response_id = "resp_123"
        msg = json.dumps(
            {
                "type": "response.audio.delta",
                "delta": "base64_audio_data",
                "item_id": "item_1",
                "content_index": 0,
                "output_index": 0,
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelResponseAudioDelta)
        self.assertEqual(event.delta, "base64_audio_data")
        self.assertEqual(event.item_id, "item_1")

    def test_parse_audio_done(self) -> None:
        """Test parsing response.audio.done event."""
        self.model._current_response_id = "resp_123"
        msg = json.dumps(
            {
                "type": "response.audio.done",
                "item_id": "item_1",
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelResponseAudioDone)

    def test_parse_transcript_delta(self) -> None:
        """Test parsing response.audio_transcript.delta event."""
        self.model._current_response_id = "resp_123"
        msg = json.dumps(
            {
                "type": "response.audio_transcript.delta",
                "delta": "Hello ",
                "item_id": "item_1",
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelResponseAudioTranscriptDelta)
        self.assertEqual(event.delta, "Hello ")

    def test_parse_transcript_done(self) -> None:
        """Test parsing response.audio_transcript.done event."""
        self.model._current_response_id = "resp_123"
        msg = json.dumps(
            {
                "type": "response.audio_transcript.done",
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelResponseAudioTranscriptDone)

    def test_parse_tool_call_delta(self) -> None:
        """Test parsing response.function_call_arguments.delta event."""
        self.model._current_response_id = "resp_123"
        msg = json.dumps(
            {
                "type": "response.function_call_arguments.delta",
                "call_id": "call_abc",
                "delta": '{"city": ',
                "name": "get_weather",
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelResponseToolUseDelta)
        self.assertEqual(event.call_id, "call_abc")
        self.assertEqual(event.delta, '{"city": ')
        self.assertEqual(event.name, "get_weather")

    def test_parse_tool_call_done(self) -> None:
        """Test parsing response.function_call_arguments.done event."""
        self.model._current_response_id = "resp_123"
        msg = json.dumps(
            {
                "type": "response.function_call_arguments.done",
                "call_id": "call_abc",
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelResponseToolUseDone)
        self.assertEqual(event.call_id, "call_abc")

    def test_parse_input_transcription_done(self) -> None:
        """Test parsing input audio transcription event."""
        msg = json.dumps(
            {
                "type": "conversation.item.input_audio_transcription."
                "completed",
                "transcript": "Hello, how are you?",
                "item_id": "item_xyz",
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelInputTranscriptionDone)
        self.assertEqual(event.transcript, "Hello, how are you?")
        self.assertEqual(event.item_id, "item_xyz")

    def test_parse_speech_started(self) -> None:
        """Test parsing speech started (VAD) event."""
        msg = json.dumps(
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "item_vad",
                "audio_start_ms": 1000,
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelInputStarted)
        self.assertEqual(event.item_id, "item_vad")
        self.assertEqual(event.audio_start_ms, 1000)

    def test_parse_speech_stopped(self) -> None:
        """Test parsing speech stopped (VAD) event."""
        msg = json.dumps(
            {
                "type": "input_audio_buffer.speech_stopped",
                "item_id": "item_vad",
                "audio_end_ms": 5000,
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelInputDone)
        self.assertEqual(event.item_id, "item_vad")
        self.assertEqual(event.audio_end_ms, 5000)

    def test_parse_error_event(self) -> None:
        """Test parsing error event."""
        msg = json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "invalid_request",
                    "code": "BAD_REQUEST",
                    "message": "Invalid audio format",
                },
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelError)
        self.assertEqual(event.error_type, "invalid_request")
        self.assertEqual(event.code, "BAD_REQUEST")
        self.assertEqual(event.message, "Invalid audio format")

    def test_parse_invalid_json(self) -> None:
        """Test parsing invalid JSON returns error event."""
        event = self.model._parse_server_message("not valid json {{{")

        self.assertIsInstance(event, ModelError)
        self.assertEqual(event.error_type, "parse_error")
        self.assertEqual(event.code, "JSON_PARSE_ERROR")

    def test_parse_unknown_event_type(self) -> None:
        """Test parsing unknown event type."""
        msg = json.dumps(
            {
                "type": "some.unknown.event",
            },
        )
        event = self.model._parse_server_message(msg)

        # Should return generic event
        self.assertEqual(event.type, ModelEventType.SESSION_UPDATED)


class TestDashScopeRealtimeModelPreprocessAudio(unittest.TestCase):
    """Test cases for audio preprocessing."""

    def setUp(self) -> None:
        """Set up test model."""
        self.model = DashScopeRealtimeModel(
            api_key="test_key",
            input_sample_rate=16000,
        )

    def test_preprocess_audio_same_rate(self) -> None:
        """Test audio preprocessing with same sample rate (no resampling)."""
        audio_data = b"\x00\x01\x02\x03"
        result = self.model._preprocess_audio(audio_data, 16000)
        self.assertEqual(result, audio_data)

    def test_preprocess_audio_no_rate(self) -> None:
        """Test audio preprocessing with no sample rate (no resampling)."""
        audio_data = b"\x00\x01\x02\x03"
        result = self.model._preprocess_audio(audio_data, None)
        self.assertEqual(result, audio_data)

    def test_preprocess_audio_different_rate(self) -> None:
        """Test audio preprocessing with different sample rate (resampling)."""
        # Create simple audio data (1 second of silence at 48kHz)
        import numpy as np

        samples = np.zeros(48000, dtype=np.int16)
        audio_data = samples.tobytes()

        result = self.model._preprocess_audio(audio_data, 48000)

        # Should be resampled to 16kHz (1/3 of samples)
        expected_samples = 16000
        actual_samples = len(result) // 2  # 16-bit = 2 bytes per sample
        self.assertEqual(actual_samples, expected_samples)
