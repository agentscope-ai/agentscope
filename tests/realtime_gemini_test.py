# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for Gemini Realtime Model class."""
import json
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agentscope.realtime import GeminiRealtimeModel, ModelEvents
from agentscope.message import (
    AudioBlock,
    TextBlock,
    ImageBlock,
    ToolResultBlock,
    Base64Source,
)


class TestGeminiRealtimeModelParseAPIMessage(IsolatedAsyncioTestCase):
    """Test parsing API messages from Gemini realtime model."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.model = GeminiRealtimeModel(
            model_name="gemini-2.5-flash-native-audio-preview",
            api_key="test_api_key",
            voice="Puck",
        )

    async def test_parse_setup_complete_event(self) -> None:
        """Test parsing setupComplete event."""
        message = json.dumps({"setupComplete": {}})

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelSessionCreatedEvent)
        self.assertEqual(event.session_id, "gemini_session")
        self.assertEqual(event.type, "model_session_created")

    async def test_parse_audio_delta_event(self) -> None:
        """Test parsing serverContent with audio data."""
        message = json.dumps(
            {
                "serverContent": {
                    "modelTurn": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "audio/pcm;rate=24000",
                                    "data": "base64_audio_chunk",
                                },
                            },
                        ],
                    },
                },
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelResponseAudioDeltaEvent)
        self.assertEqual(event.delta, "base64_audio_chunk")
        self.assertEqual(event.format.type, "audio/pcm")
        self.assertEqual(event.format.rate, 24000)
        self.assertEqual(event.type, "model_response_audio_delta")

    async def test_parse_output_transcription_event(self) -> None:
        """Test parsing serverContent with output transcription."""
        message = json.dumps(
            {
                "serverContent": {
                    "outputTranscription": {
                        "text": "Hello there",
                    },
                },
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(
            event,
            ModelEvents.ModelResponseAudioTranscriptDeltaEvent,
        )
        self.assertEqual(event.delta, "Hello there")

    async def test_parse_input_transcription_event(self) -> None:
        """Test parsing serverContent with input transcription."""
        message = json.dumps(
            {
                "serverContent": {
                    "inputTranscription": {
                        "text": "How are you?",
                    },
                },
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(
            event,
            ModelEvents.ModelInputTranscriptionDoneEvent,
        )
        self.assertEqual(event.transcript, "How are you?")

    async def test_parse_generation_complete_event(self) -> None:
        """Test parsing serverContent with generationComplete."""
        self.model._response_id = "resp_123"

        message = json.dumps(
            {
                "serverContent": {
                    "generationComplete": True,
                },
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelResponseDoneEvent)
        self.assertEqual(event.response_id, "resp_123")
        self.assertIsNone(self.model._response_id)

    async def test_parse_turn_complete_event(self) -> None:
        """Test parsing serverContent with turnComplete."""
        self.model._response_id = "resp_456"

        message = json.dumps(
            {
                "serverContent": {
                    "turnComplete": True,
                },
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelResponseDoneEvent)
        self.assertEqual(event.response_id, "resp_456")
        self.assertIsNone(self.model._response_id)

    async def test_parse_tool_call_event(self) -> None:
        """Test parsing toolCall event."""
        message = json.dumps(
            {
                "toolCall": {
                    "functionCalls": [
                        {
                            "name": "get_weather",
                            "id": "call_789",
                            "args": {"location": "San Francisco"},
                        },
                    ],
                },
            },
        )

        events = await self.model.parse_api_message(message)

        self.assertIsInstance(events, list)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIsInstance(
            event,
            ModelEvents.ModelResponseToolUseDoneEvent,
        )
        self.assertEqual(event.tool_use["name"], "get_weather")
        self.assertEqual(event.tool_use["id"], "call_789")
        self.assertEqual(
            event.tool_use["input"],
            {"location": "San Francisco"},
        )

    async def test_parse_error_event(self) -> None:
        """Test parsing error event."""
        message = json.dumps(
            {
                "error": {
                    "status": "INVALID_ARGUMENT",
                    "code": 400,
                    "message": "Invalid request",
                },
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelErrorEvent)
        self.assertEqual(event.error_type, "INVALID_ARGUMENT")
        self.assertEqual(event.code, "400")
        self.assertEqual(event.message, "Invalid request")
        self.assertEqual(event.type, "model_error")


class TestGeminiRealtimeModelSend(IsolatedAsyncioTestCase):
    """Test sending data to Gemini realtime model."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        from websockets import State

        self.model = GeminiRealtimeModel(
            model_name="gemini-2.5-flash-native-audio-preview",
            api_key="test_api_key",
            voice="Puck",
        )
        self.mock_websocket = AsyncMock()
        self.mock_websocket.state = State.OPEN
        self.model._websocket = self.mock_websocket

    async def test_send_audio_base64(self) -> None:
        """Test sending audio data with base64 source."""
        audio_data = AudioBlock(
            type="audio",
            source=Base64Source(
                type="base64",
                media_type="audio/wav",
                data="base64_encoded_audio_data",
            ),
        )

        await self.model.send(audio_data)

        self.mock_websocket.send.assert_called_once()

        sent_message = self.mock_websocket.send.call_args[0][0]
        sent_data = json.loads(sent_message)

        self.assertIn("realtimeInput", sent_data)
        self.assertEqual(
            sent_data["realtimeInput"]["audio"]["data"],
            "base64_encoded_audio_data",
        )

    async def test_send_image_base64(self) -> None:
        """Test sending image data with base64 source."""
        image_data = ImageBlock(
            type="image",
            source=Base64Source(
                type="base64",
                media_type="image/jpeg",
                data="base64_encoded_image_data",
            ),
        )

        await self.model.send(image_data)

        self.mock_websocket.send.assert_called_once()

        sent_message = self.mock_websocket.send.call_args[0][0]
        sent_data = json.loads(sent_message)

        self.assertIn("realtimeInput", sent_data)
        self.assertEqual(
            sent_data["realtimeInput"]["video"]["data"],
            "base64_encoded_image_data",
        )

    async def test_send_text(self) -> None:
        """Test sending text data."""
        text_data = TextBlock(
            type="text",
            text="Hello, how are you?",
        )

        await self.model.send(text_data)

        self.mock_websocket.send.assert_called_once()

        sent_message = self.mock_websocket.send.call_args[0][0]
        sent_data = json.loads(sent_message)

        self.assertIn("clientContent", sent_data)
        turns = sent_data["clientContent"]["turns"]
        self.assertEqual(turns[0]["parts"][0]["text"], "Hello, how are you?")

    async def test_send_tool_result(self) -> None:
        """Test sending tool result data."""
        tool_result = ToolResultBlock(
            type="tool_result",
            id="call_123",
            output="The weather is sunny",
            name="get_weather",
        )

        await self.model.send(tool_result)

        self.mock_websocket.send.assert_called_once()

        sent_message = self.mock_websocket.send.call_args[0][0]
        sent_data = json.loads(sent_message)

        self.assertIn("toolResponse", sent_data)
        func_responses = sent_data["toolResponse"]["functionResponses"]
        self.assertEqual(len(func_responses), 1)
        self.assertEqual(func_responses[0]["id"], "call_123")
        self.assertEqual(func_responses[0]["name"], "get_weather")
