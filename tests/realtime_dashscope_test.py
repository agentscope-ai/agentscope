# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for DashScope Realtime Model class."""
import json
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.realtime import DashScopeRealtimeModel, ModelEvents
from agentscope.message import (
    AudioBlock,
    ImageBlock,
    Base64Source,
    URLSource,
)


class TestDashScopeRealtimeModelParseAPIMessage(IsolatedAsyncioTestCase):
    """Test parsing API messages from DashScope realtime model."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.model = DashScopeRealtimeModel(
            model_name="qwen3-omni-flash-realtime",
            api_key="test_api_key",
            voice="Cherry",
        )

    async def test_parse_session_created_event(self) -> None:
        """Test parsing session.created event."""
        message = json.dumps(
            {
                "type": "session.created",
                "session": {
                    "id": "session_123",
                },
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelSessionCreatedEvent)
        self.assertEqual(event.session_id, "session_123")
        self.assertEqual(event.type, "model_session_created")

    async def test_parse_response_created_event(self) -> None:
        """Test parsing response.created event."""
        message = json.dumps(
            {
                "type": "response.created",
                "response": {
                    "id": "resp_456",
                },
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelResponseCreatedEvent)
        self.assertEqual(event.response_id, "resp_456")
        self.assertEqual(event.type, "model_response_created")
        # Check that response_id is stored internally
        self.assertEqual(self.model._response_id, "resp_456")

    async def test_parse_response_done_event(self) -> None:
        """Test parsing response.done event."""
        # Set up the internal response_id
        self.model._response_id = "resp_789"

        message = json.dumps(
            {
                "type": "response.done",
                "response": {
                    "id": "resp_789",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                    },
                },
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelResponseDoneEvent)
        self.assertEqual(event.response_id, "resp_789")
        self.assertEqual(event.input_tokens, 100)
        self.assertEqual(event.output_tokens, 50)
        self.assertEqual(event.type, "model_response_done")
        # Check that response_id is cleared
        self.assertEqual(self.model._response_id, "")

    async def test_parse_response_audio_delta_event(self) -> None:
        """Test parsing response.audio.delta event."""
        self.model._response_id = "resp_audio_1"

        message = json.dumps(
            {
                "type": "response.audio.delta",
                "item_id": "item_audio_1",
                "delta": "base64_audio_data_chunk",
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelResponseAudioDeltaEvent)
        self.assertEqual(event.response_id, "resp_audio_1")
        self.assertEqual(event.item_id, "item_audio_1")
        self.assertEqual(event.delta, "base64_audio_data_chunk")
        self.assertEqual(event.format.type, "audio/pcm")
        self.assertEqual(event.format.rate, 24000)
        self.assertEqual(event.type, "model_response_audio_delta")

    async def test_parse_response_audio_done_event(self) -> None:
        """Test parsing response.audio.done event."""
        self.model._response_id = "resp_audio_2"

        message = json.dumps(
            {
                "type": "response.audio.done",
                "item_id": "item_audio_2",
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelResponseAudioDoneEvent)
        self.assertEqual(event.response_id, "resp_audio_2")
        self.assertEqual(event.item_id, "item_audio_2")
        self.assertEqual(event.type, "model_response_audio_done")

    async def test_parse_response_audio_transcript_delta_event(self) -> None:
        """Test parsing response.audio_transcript.delta event."""
        self.model._response_id = "resp_transcript_1"

        message = json.dumps(
            {
                "type": "response.audio_transcript.delta",
                "item_id": "item_transcript_1",
                "delta": "Hello ",
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(
            event,
            ModelEvents.ModelResponseAudioTranscriptDeltaEvent,
        )
        self.assertEqual(event.response_id, "resp_transcript_1")
        self.assertEqual(event.item_id, "item_transcript_1")
        self.assertEqual(event.delta, "Hello ")
        self.assertEqual(
            event.type,
            "model_response_audio_transcript_delta",
        )

    async def test_parse_response_audio_transcript_done_event(self) -> None:
        """Test parsing response.audio_transcript.done event."""
        self.model._response_id = "resp_transcript_2"

        message = json.dumps(
            {
                "type": "response.audio_transcript.done",
                "item_id": "item_transcript_2",
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(
            event,
            ModelEvents.ModelResponseAudioTranscriptDoneEvent,
        )
        self.assertEqual(event.response_id, "resp_transcript_2")
        self.assertEqual(event.item_id, "item_transcript_2")
        self.assertEqual(event.type, "model_response_audio_transcript_done")

    async def test_parse_input_audio_transcription_completed_event(
        self,
    ) -> None:
        """Test parsing conversation.item.input_audio_transcription.completed
        event."""
        message = json.dumps(
            {
                "type": "conversation.item.input_audio_transcription."
                "completed",
                "item_id": "item_input_1",
                "transcript": "Hello world",
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(
            event,
            ModelEvents.ModelInputTranscriptionDoneEvent,
        )
        self.assertEqual(event.item_id, "item_input_1")
        self.assertEqual(event.transcript, "Hello world")
        self.assertEqual(event.type, "model_input_transcription_done")

    async def test_parse_input_audio_buffer_speech_started_event(
        self,
    ) -> None:
        """Test parsing input_audio_buffer.speech_started event."""
        message = json.dumps(
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "item_vad_1",
                "audio_start_ms": 1000,
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelInputStartedEvent)
        self.assertEqual(event.item_id, "item_vad_1")
        self.assertEqual(event.audio_start_ms, 1000)
        self.assertEqual(event.type, "model_input_started")

    async def test_parse_input_audio_buffer_speech_stopped_event(
        self,
    ) -> None:
        """Test parsing input_audio_buffer.speech_stopped event."""
        message = json.dumps(
            {
                "type": "input_audio_buffer.speech_stopped",
                "item_id": "item_vad_2",
                "audio_end_ms": 5000,
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelInputDoneEvent)
        self.assertEqual(event.item_id, "item_vad_2")
        self.assertEqual(event.audio_end_ms, 5000)
        self.assertEqual(event.type, "model_input_done")

    async def test_parse_error_event(self) -> None:
        """Test parsing error event."""
        message = json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "invalid_request",
                    "code": "400",
                    "message": "Invalid request format",
                },
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelErrorEvent)
        self.assertEqual(event.error_type, "invalid_request")
        self.assertEqual(event.code, "400")
        self.assertEqual(event.message, "Invalid request format")
        self.assertEqual(event.type, "model_error")


class TestDashScopeRealtimeModelSend(IsolatedAsyncioTestCase):
    """Test sending data to DashScope realtime model."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        from websockets import State

        self.model = DashScopeRealtimeModel(
            model_name="qwen3-omni-flash-realtime",
            api_key="test_api_key",
            voice="Cherry",
        )
        # Mock the websocket
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

        # Verify websocket.send was called
        self.mock_websocket.send.assert_called_once()

        # Parse the send message
        sent_message = self.mock_websocket.send.call_args[0][0]
        sent_data = json.loads(sent_message)

        self.assertEqual(sent_data["type"], "input_audio_buffer.append")
        self.assertEqual(sent_data["audio"], "base64_encoded_audio_data")

    async def test_send_image_base64(self) -> None:
        """Test sending image data with base64 source."""
        image_data = ImageBlock(
            type="image",
            source=Base64Source(
                type="base64",
                media_type="image/png",
                data="base64_encoded_image_data",
            ),
        )

        await self.model.send(image_data)

        # Verify websocket.send was called
        self.mock_websocket.send.assert_called_once()

        # Parse the send message
        sent_message = self.mock_websocket.send.call_args[0][0]
        sent_data = json.loads(sent_message)

        self.assertEqual(sent_data["type"], "input_image_buffer.append")
        self.assertEqual(sent_data["image"], "base64_encoded_image_data")

    async def test_send_image_url(self) -> None:
        """Test sending image data with URL source."""
        image_data = ImageBlock(
            type="image",
            source=URLSource(
                type="url",
                url="https://example.com/image.jpg",
            ),
        )

        with patch(
            "agentscope.realtime._dashscope_realtime_model."
            "_get_bytes_from_web_url",
        ) as mock_get_bytes:
            mock_get_bytes.return_value = "fetched_image_bytes"

            await self.model.send(image_data)

            # Verify URL was fetched
            mock_get_bytes.assert_called_once_with(
                "https://example.com/image.jpg",
            )

            # Verify websocket.send was called
            self.mock_websocket.send.assert_called_once()

            # Parse the sent message
            sent_message = self.mock_websocket.send.call_args[0][0]
            sent_data = json.loads(sent_message)

            self.assertEqual(sent_data["type"], "input_image_url.append")
            self.assertEqual(sent_data["image_url"], "fetched_image_bytes")

    # async def test_send_text(self) -> None:
    #     """Test sending text data."""
    #     text_data = TextBlock(
    #         type="text",
    #         text="Hello, how are you?",
    #     )
    #
    #     with patch("shortuuid.uuid") as mock_uuid:
    #         mock_uuid.return_value = "test_uuid_123"
    #
    #         await self.model.send(text_data)
    #
    #         # Verify websocket.send was called
    #         self.mock_websocket.send.assert_called_once()
    #
    #         # Parse the sent message
    #         sent_message = self.mock_websocket.send.call_args[0][0]
    #         sent_data = json.loads(sent_message)
    #
    #         self.assertEqual(sent_data["event_id"], "test_uuid_123")
    #         self.assertEqual(sent_data["type"], "response.create")
    #         self.assertEqual(
    #             sent_data["response"]["instructions"],
    #             "Hello, how are you?",
    #         )
