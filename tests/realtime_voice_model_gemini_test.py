# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for GeminiRealtimeModel class."""
import json
import unittest
from unittest.mock import patch

from agentscope.agent.realtime_voice_agent import (
    GeminiRealtimeModel,
)
from agentscope.agent.realtime_voice_agent.events import (
    ModelEventType,
    ModelSessionCreated,
    ModelResponseAudioDelta,
    ModelResponseAudioTranscriptDelta,
    ModelResponseToolUseDone,
    ModelResponseDone,
    ModelInputTranscriptionDone,
    ModelError,
)


class TestGeminiRealtimeModelInit(unittest.TestCase):
    """Test cases for GeminiRealtimeModel initialization."""

    def test_init_default_params(self) -> None:
        """Test initialization with default parameters."""
        model = GeminiRealtimeModel(
            api_key="test_key",
        )
        self.assertEqual(model.api_key, "test_key")
        self.assertEqual(
            model.model_name,
            "gemini-2.5-flash-native-audio-preview-12-2025",
        )
        self.assertEqual(model.voice, "Puck")
        self.assertEqual(model.instructions, "You are a helpful assistant.")
        self.assertTrue(model.vad_enabled)
        self.assertTrue(model.enable_input_audio_transcription)
        self.assertTrue(model.enable_output_audio_transcription)
        self.assertEqual(model.response_modalities, ["AUDIO"])
        self.assertFalse(model.enable_thinking)
        self.assertIsNone(model.thinking_budget)
        self.assertFalse(model.session_resumption)
        self.assertEqual(
            model.base_url,
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService."
            "BidiGenerateContent",
        )

    def test_init_custom_params(self) -> None:
        """Test initialization with custom parameters."""
        model = GeminiRealtimeModel(
            api_key="custom_key",
            model_name="gemini-2.5-pro",
            voice="Charon",
            instructions="Be concise.",
            response_modalities=["AUDIO"],
            enable_thinking=True,
            thinking_budget=1024,
            session_resumption=True,
            vad_enabled=False,
            enable_input_audio_transcription=False,
            enable_output_audio_transcription=False,
            base_url="wss://custom.gemini.example.com/ws",
            generate_kwargs={"temperature": 0.7},
        )
        self.assertEqual(model.api_key, "custom_key")
        self.assertEqual(model.model_name, "gemini-2.5-pro")
        self.assertEqual(model.voice, "Charon")
        self.assertEqual(model.instructions, "Be concise.")
        self.assertEqual(model.response_modalities, ["AUDIO"])
        self.assertTrue(model.enable_thinking)
        self.assertEqual(model.thinking_budget, 1024)
        self.assertTrue(model.session_resumption)
        self.assertFalse(model.vad_enabled)
        self.assertFalse(model.enable_input_audio_transcription)
        self.assertFalse(model.enable_output_audio_transcription)
        self.assertEqual(model.base_url, "wss://custom.gemini.example.com/ws")
        self.assertEqual(model.generate_kwargs, {"temperature": 0.7})

    def test_provider_name(self) -> None:
        """Test provider_name property."""
        model = GeminiRealtimeModel(api_key="test")
        self.assertEqual(model.provider_name, "gemini")

    def test_supports_image(self) -> None:
        """Test supports_image property."""
        model = GeminiRealtimeModel(api_key="test")
        self.assertTrue(model.supports_image)


class TestGeminiRealtimeModelConfig(unittest.TestCase):
    """Test cases for configuration methods."""

    def setUp(self) -> None:
        """Set up test model."""
        self.model = GeminiRealtimeModel(
            api_key="test_key",
            model_name="test-model",
            voice="Puck",
            instructions="Test instructions",
        )

    def test_get_websocket_url(self) -> None:
        """Test WebSocket URL generation."""
        url = self.model._get_websocket_url()

        expected = (
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService."
            "BidiGenerateContent?key=test_key"
        )

        self.assertEqual(url, expected)

    def test_get_websocket_url_custom_base(self) -> None:
        """Test WebSocket URL generation with custom base_url."""
        model = GeminiRealtimeModel(
            api_key="test_key",
            base_url="wss://custom.gemini.example.com/ws",
        )
        url = model._get_websocket_url()
        self.assertEqual(
            url,
            "wss://custom.gemini.example.com/ws?key=test_key",
        )

    def test_get_headers(self) -> None:
        """Test HTTP headers."""
        headers = self.model._get_headers()
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_build_session_config_basic(self) -> None:
        """Test basic session config (setup message)."""
        config_str = self.model._build_session_config()
        config = json.loads(config_str)

        self.assertIn("setup", config)
        setup = config["setup"]
        self.assertEqual(setup["model"], "models/test-model")
        self.assertIn("generationConfig", setup)
        self.assertEqual(
            setup["generationConfig"]["responseModalities"],
            ["AUDIO"],
        )
        self.assertIn("speechConfig", setup["generationConfig"])

    def test_build_session_config_with_transcription(self) -> None:
        """Test session config with transcription enabled."""
        self.model.enable_input_audio_transcription = True
        self.model.enable_output_audio_transcription = True
        config_str = self.model._build_session_config()
        config = json.loads(config_str)

        setup = config["setup"]
        self.assertIn("inputAudioTranscription", setup)
        self.assertIn("outputAudioTranscription", setup)

    def test_build_session_config_without_transcription(self) -> None:
        """Test session config with transcription disabled."""
        self.model.enable_input_audio_transcription = False
        self.model.enable_output_audio_transcription = False
        config_str = self.model._build_session_config()
        config = json.loads(config_str)

        setup = config["setup"]
        self.assertNotIn("inputAudioTranscription", setup)
        self.assertNotIn("outputAudioTranscription", setup)

    def test_build_session_config_with_thinking(self) -> None:
        """Test session config with thinking enabled."""
        self.model.enable_thinking = True
        self.model.thinking_budget = 2048
        config_str = self.model._build_session_config()
        config = json.loads(config_str)

        thinking_config = config["setup"]["generationConfig"]["thinkingConfig"]
        self.assertTrue(thinking_config["includeThoughts"])
        self.assertEqual(thinking_config["thinkingBudget"], 2048)

    def test_build_session_config_without_vad(self) -> None:
        """Test session config with VAD disabled."""
        self.model.vad_enabled = False
        config_str = self.model._build_session_config()
        config = json.loads(config_str)

        setup = config["setup"]
        self.assertIn("realtimeInputConfig", setup)
        self.assertTrue(
            setup["realtimeInputConfig"]["automaticActivityDetection"][
                "disabled"
            ],
        )

    def test_build_session_config_with_system_instruction(self) -> None:
        """Test session config includes system instruction."""
        config_str = self.model._build_session_config()
        config = json.loads(config_str)

        setup = config["setup"]
        self.assertIn("systemInstruction", setup)
        self.assertEqual(
            setup["systemInstruction"]["parts"][0]["text"],
            "Test instructions",
        )

    def test_build_session_config_with_custom_instructions(self) -> None:
        """Test session config with custom instructions override."""
        config_str = self.model._build_session_config(
            instructions="Override instructions",
        )
        config = json.loads(config_str)

        setup = config["setup"]
        self.assertEqual(
            setup["systemInstruction"]["parts"][0]["text"],
            "Override instructions",
        )


class TestGeminiRealtimeModelFormatMessages(unittest.TestCase):
    """Test cases for message formatting methods."""

    def setUp(self) -> None:
        """Set up test model."""
        self.model = GeminiRealtimeModel(api_key="test_key")

    def test_format_audio_message(self) -> None:
        """Test audio message formatting."""
        audio_b64 = "SGVsbG8gV29ybGQ="
        msg_str = self.model._format_audio_message(audio_b64)
        msg = json.loads(msg_str)

        self.assertIn("realtimeInput", msg)
        audio = msg["realtimeInput"]["audio"]
        self.assertEqual(audio["mimeType"], "audio/pcm;rate=16000")
        self.assertEqual(audio["data"], audio_b64)

    def test_format_image_message(self) -> None:
        """Test image message formatting."""
        image_b64 = "/9j/4AAQSkZJRg..."
        msg_str = self.model._format_image_message(image_b64)
        msg = json.loads(msg_str)

        self.assertIn("realtimeInput", msg)
        # Gemini uses "video" key for image/video input
        media = msg["realtimeInput"]["video"]
        self.assertEqual(media["mimeType"], "image/jpeg")
        self.assertEqual(media["data"], image_b64)

    def test_format_image_message_with_png(self) -> None:
        """Test image message formatting with PNG."""
        image_b64 = "iVBORw0KGgo..."
        msg_str = self.model._format_image_message(image_b64, "image/png")
        msg = json.loads(msg_str)

        # Gemini uses "video" key for image/video input
        media = msg["realtimeInput"]["video"]
        self.assertEqual(media["mimeType"], "image/png")

    def test_format_text_message(self) -> None:
        """Test text message formatting."""
        msg_str = self.model._format_text_message("Hello")
        msg = json.loads(msg_str)

        self.assertIn("clientContent", msg)
        content = msg["clientContent"]
        self.assertTrue(content["turnComplete"])
        self.assertEqual(content["turns"][0]["role"], "user")
        self.assertEqual(content["turns"][0]["parts"][0]["text"], "Hello")

    def test_format_tool_result_message(self) -> None:
        """Test tool result message formatting."""
        msg_str = self.model._format_tool_result_message(
            tool_id="call_123",
            tool_name="get_weather",
            result='{"temp": 22}',
        )
        msg = json.loads(msg_str)

        self.assertIn("toolResponse", msg)
        responses = msg["toolResponse"]["functionResponses"]
        self.assertEqual(responses[0]["id"], "call_123")
        self.assertEqual(responses[0]["name"], "get_weather")
        # Gemini parses JSON result into object
        self.assertEqual(responses[0]["response"]["temp"], 22)

    def test_format_cancel_message(self) -> None:
        """Test cancel message formatting.

        Gemini uses clientContent with turnComplete to interrupt responses.
        """
        msg_str = self.model._format_cancel_message()
        msg = json.loads(msg_str)

        self.assertIn("clientContent", msg)
        self.assertTrue(msg["clientContent"]["turnComplete"])
        # Gemini requires turns field even if empty
        self.assertIn("turns", msg["clientContent"])


class TestGeminiRealtimeModelSessionUpdate(unittest.TestCase):
    """Test cases for session update message formatting."""

    def setUp(self) -> None:
        """Set up test model."""
        self.model = GeminiRealtimeModel(api_key="test_key")

    def test_format_session_update_returns_none(self) -> None:
        """Test session update returns None (Gemini doesn't support it)."""
        # Gemini Live API does not support dynamic session update
        # Session must be configured at connection time
        with patch(
            "agentscope.agent.realtime_voice_agent.model_gemini.logger",
        ) as mock_logger:
            self.assertIsNone(
                self.model._format_session_update_message({"voice": "Charon"}),
            )
            mock_logger.warning.assert_called_once()

    def test_format_session_update_empty_config(self) -> None:
        """Test session update with empty config returns None."""
        with patch(
            "agentscope.agent.realtime_voice_agent.model_gemini.logger",
        ):
            self.assertIsNone(self.model._format_session_update_message({}))


class TestGeminiRealtimeModelParseServerMessage(unittest.TestCase):
    """Test cases for server message parsing."""

    def setUp(self) -> None:
        """Set up test model."""
        self.model = GeminiRealtimeModel(api_key="test_key")

    def test_parse_setup_complete(self) -> None:
        """Test parsing setupComplete event."""
        msg = json.dumps(
            {
                "setupComplete": {},
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelSessionCreated)

    def test_parse_server_content_with_audio(self) -> None:
        """Test parsing serverContent with audio."""
        self.model._is_in_response = True
        self.model._current_response_id = "resp_123"
        msg = json.dumps(
            {
                "serverContent": {
                    "modelTurn": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "audio/pcm",
                                    "data": "base64_audio",
                                },
                            },
                        ],
                    },
                },
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelResponseAudioDelta)
        self.assertEqual(event.delta, "base64_audio")

    def test_parse_server_content_with_text(self) -> None:
        """Test parsing serverContent with text.

        Note: Text content is emitted via _emit_event callback,
        the return value is SESSION_UPDATED when there's no audio.
        """
        self.model._is_in_response = True
        self.model._current_response_id = "resp_123"

        # Capture emitted events
        emitted_events: list = []
        self.model._emit_event = emitted_events.append

        msg = json.dumps(
            {
                "serverContent": {
                    "modelTurn": {
                        "parts": [
                            {"text": "Hello world"},
                        ],
                    },
                },
            },
        )
        event = self.model._parse_server_message(msg)

        # Return value is SESSION_UPDATED when no audio
        self.assertEqual(event.type, ModelEventType.SESSION_UPDATED)
        # Text is emitted via callback
        self.assertEqual(len(emitted_events), 1)
        self.assertIsInstance(
            emitted_events[0],
            ModelResponseAudioTranscriptDelta,
        )
        self.assertEqual(emitted_events[0].delta, "Hello world")

    def test_parse_server_content_turn_complete(self) -> None:
        """Test parsing serverContent with turnComplete."""
        self.model._is_in_response = True
        self.model._current_response_id = "resp_123"
        msg = json.dumps(
            {
                "serverContent": {
                    "turnComplete": True,
                },
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelResponseDone)
        self.assertFalse(self.model._is_in_response)

    def test_parse_server_content_input_transcription(self) -> None:
        """Test parsing serverContent with inputTranscription."""
        msg = json.dumps(
            {
                "serverContent": {
                    "inputTranscription": {
                        "text": "Hello, how are you?",
                    },
                },
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelInputTranscriptionDone)
        self.assertEqual(event.transcript, "Hello, how are you?")

    def test_parse_server_content_output_transcription(self) -> None:
        """Test parsing serverContent with outputTranscription."""
        self.model._is_in_response = True
        self.model._current_response_id = "resp_123"
        msg = json.dumps(
            {
                "serverContent": {
                    "outputTranscription": {
                        "text": "I am fine",
                    },
                },
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelResponseAudioTranscriptDelta)
        self.assertEqual(event.delta, "I am fine")

    def test_parse_tool_call(self) -> None:
        """Test parsing toolCall event."""
        self.model._is_in_response = True
        self.model._current_response_id = "resp_123"
        msg = json.dumps(
            {
                "toolCall": {
                    "functionCalls": [
                        {
                            "id": "call_abc",
                            "name": "get_weather",
                            "args": {"city": "Beijing"},
                        },
                    ],
                },
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelResponseToolUseDone)
        self.assertEqual(event.call_id, "call_abc")

    def test_parse_error_event(self) -> None:
        """Test parsing error event."""
        msg = json.dumps(
            {
                "error": {
                    "code": 400,
                    "message": "Invalid request",
                },
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertIsInstance(event, ModelError)
        self.assertEqual(event.code, "400")
        self.assertEqual(event.message, "Invalid request")

    def test_parse_invalid_json(self) -> None:
        """Test parsing invalid JSON returns error event."""
        event = self.model._parse_server_message("not valid json {{{")

        self.assertIsInstance(event, ModelError)
        self.assertEqual(event.error_type, "parse_error")

    def test_parse_unknown_event_type(self) -> None:
        """Test parsing unknown event type."""
        msg = json.dumps(
            {
                "someUnknownField": {},
            },
        )
        event = self.model._parse_server_message(msg)

        self.assertEqual(event.type, ModelEventType.SESSION_UPDATED)

    def test_parse_filters_thought_parts(self) -> None:
        """Test that thought parts are filtered out."""
        self.model._is_in_response = True
        self.model._current_response_id = "resp_123"

        # Capture emitted events
        emitted_events: list = []
        self.model._emit_event = emitted_events.append

        msg = json.dumps(
            {
                "serverContent": {
                    "modelTurn": {
                        "parts": [
                            {"text": "Internal reasoning", "thought": True},
                            {"text": "Actual response"},
                        ],
                    },
                },
            },
        )
        self.model._parse_server_message(msg)

        # Should only get the non-thought part
        self.assertEqual(len(emitted_events), 1)
        self.assertIsInstance(
            emitted_events[0],
            ModelResponseAudioTranscriptDelta,
        )
        self.assertEqual(emitted_events[0].delta, "Actual response")

    def test_parse_handles_none_transcription(self) -> None:
        """Test that None transcription is handled gracefully."""
        msg = json.dumps(
            {
                "serverContent": {
                    "inputTranscription": None,
                },
            },
        )
        # Should not raise an error
        event = self.model._parse_server_message(msg)
        self.assertEqual(event.type, ModelEventType.SESSION_UPDATED)


class TestGeminiRealtimeModelResponseIdGeneration(unittest.TestCase):
    """Test cases for response ID generation."""

    def setUp(self) -> None:
        """Set up test model."""
        self.model = GeminiRealtimeModel(api_key="test_key")

    def test_response_id_increments(self) -> None:
        """Test that response IDs increment properly."""
        self.model._ensure_response_started()
        first_id = self.model._current_response_id

        # Simulate response end
        self.model._is_in_response = False

        self.model._ensure_response_started()
        second_id = self.model._current_response_id

        self.assertNotEqual(first_id, second_id)
        self.assertIn("resp_gemini_1", first_id)
        self.assertIn("resp_gemini_2", second_id)

    def test_ensure_response_started_idempotent(self) -> None:
        """Test that _ensure_response_started is idempotent during response."""
        self.model._ensure_response_started()
        first_id = self.model._current_response_id

        self.model._ensure_response_started()
        second_id = self.model._current_response_id

        self.assertEqual(first_id, second_id)


class TestGeminiRealtimeModelToolkitFormat(unittest.TestCase):
    """Test cases for toolkit schema formatting."""

    def setUp(self) -> None:
        """Set up test model."""
        self.model = GeminiRealtimeModel(api_key="test_key")

    def test_format_toolkit_schema(self) -> None:
        """Test toolkit schema formatting."""
        schemas = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather info",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string"},
                        },
                    },
                },
            },
        ]
        result = self.model._format_toolkit_schema(schemas)

        self.assertEqual(len(result), 1)
        self.assertIn("function_declarations", result[0])
        self.assertEqual(
            result[0]["function_declarations"][0]["name"],
            "get_weather",
        )
