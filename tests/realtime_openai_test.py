# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for OpenAI Realtime Model class."""
import json
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.realtime import OpenAIRealtimeModel, ModelEvents
from agentscope.message import (
    AudioBlock,
    TextBlock,
    ToolResultBlock,
    Base64Source,
    URLSource,
)


class TestOpenAIRealtimeModelParseAPIMessage(IsolatedAsyncioTestCase):
    """Test parsing API messages from OpenAI realtime model."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.model = OpenAIRealtimeModel(
            model_name="gpt-4o-realtime-preview",
            api_key="test_api_key",
            voice="alloy",
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
        self.assertEqual(self.model._response_id, "resp_456")

    async def test_parse_response_done_event(self) -> None:
        """Test parsing response.done event."""
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
        self.assertEqual(self.model._response_id, "")

    async def test_parse_response_audio_delta_event(self) -> None:
        """Test parsing response.output_audio.delta event."""
        self.model._response_id = "resp_audio_1"

        message = json.dumps(
            {
                "type": "response.output_audio.delta",
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
        """Test parsing response.output_audio.done event."""
        self.model._response_id = "resp_audio_2"

        message = json.dumps(
            {
                "type": "response.output_audio.done",
                "item_id": "item_audio_2",
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(event, ModelEvents.ModelResponseAudioDoneEvent)
        self.assertEqual(event.response_id, "resp_audio_2")
        self.assertEqual(event.item_id, "item_audio_2")
        self.assertEqual(event.type, "model_response_audio_done")

    async def test_parse_response_audio_transcript_delta_event(self) -> None:
        """Test parsing response.output_audio_transcript.delta event."""
        self.model._response_id = "resp_transcript_1"

        message = json.dumps(
            {
                "type": "response.output_audio_transcript.delta",
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
        """Test parsing response.output_audio_transcript.done event."""
        self.model._response_id = "resp_transcript_2"

        message = json.dumps(
            {
                "type": "response.output_audio_transcript.done",
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

    async def test_parse_function_call_arguments_delta_event(self) -> None:
        """Test parsing response.function_call_arguments.delta event."""
        self.model._response_id = "resp_tool_1"

        message = json.dumps(
            {
                "type": "response.function_call_arguments.delta",
                "call_id": "call_123",
                "item_id": "item_tool_1",
                "name": "get_weather",
                "delta": '{"location": "San',
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(
            event,
            ModelEvents.ModelResponseToolUseDeltaEvent,
        )
        self.assertEqual(event.response_id, "resp_tool_1")
        self.assertEqual(event.item_id, "item_tool_1")
        self.assertEqual(event.tool_use["id"], "call_123")
        self.assertEqual(event.tool_use["name"], "get_weather")

    async def test_parse_function_call_arguments_done_event(self) -> None:
        """Test parsing response.function_call_arguments.done event."""
        self.model._response_id = "resp_tool_2"
        self.model._tool_args_accumulator[
            "call_456"
        ] = '{"location": "San Francisco"}'

        message = json.dumps(
            {
                "type": "response.function_call_arguments.done",
                "call_id": "call_456",
                "item_id": "item_tool_2",
                "name": "get_weather",
                "arguments": '{"location": "San Francisco"}',
            },
        )

        event = await self.model.parse_api_message(message)

        self.assertIsInstance(
            event,
            ModelEvents.ModelResponseToolUseDoneEvent,
        )
        self.assertEqual(event.response_id, "resp_tool_2")
        self.assertEqual(event.item_id, "item_tool_2")
        self.assertEqual(event.tool_use["id"], "call_456")
        self.assertEqual(event.tool_use["name"], "get_weather")
        self.assertEqual(
            event.tool_use["input"],
            {"location": "San Francisco"},
        )
        self.assertNotIn("call_456", self.model._tool_args_accumulator)

    async def test_parse_input_audio_transcription_completed_event(
        self,
    ) -> None:
        """Test parsing
        conversation.item.input_audio_transcription.completed event."""
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


class TestOpenAIRealtimeModelSend(IsolatedAsyncioTestCase):
    """Test sending data to OpenAI realtime model."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        from websockets import State

        self.model = OpenAIRealtimeModel(
            model_name="gpt-4o-realtime-preview",
            api_key="test_api_key",
            voice="alloy",
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

        self.assertEqual(sent_data["type"], "input_audio_buffer.append")
        self.assertEqual(sent_data["audio"], "base64_encoded_audio_data")

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

        self.assertEqual(sent_data["type"], "conversation.item.create")
        self.assertEqual(sent_data["item"]["type"], "message")
        self.assertEqual(sent_data["item"]["role"], "user")
        self.assertEqual(
            sent_data["item"]["content"][0]["text"],
            "Hello, how are you?",
        )

    async def test_send_tool_result(self) -> None:
        """Test sending tool result data."""
        tool_result = ToolResultBlock(
            type="tool_result",
            id="call_123",
            output="The weather is sunny, 25°C",
            name="get_weather",
        )

        await self.model.send(tool_result)

        self.mock_websocket.send.assert_called_once()

        sent_message = self.mock_websocket.send.call_args[0][0]
        sent_data = json.loads(sent_message)

        self.assertEqual(sent_data["type"], "conversation.item.create")
        self.assertEqual(sent_data["item"]["type"], "function_call_output")
        self.assertEqual(sent_data["item"]["call_id"], "call_123")
        self.assertEqual(
            sent_data["item"]["output"],
            "The weather is sunny, 25°C",
        )

    async def test_send_audio_url(self) -> None:
        """Test sending audio data with URL source."""
        audio_data = AudioBlock(
            type="audio",
            source=URLSource(
                type="url",
                url="https://example.com/audio.wav",
            ),
        )

        with patch(
            "agentscope.realtime._openai_realtime_model."
            "_get_bytes_from_web_url",
        ) as mock_get_bytes:
            mock_get_bytes.return_value = "fetched_audio_bytes"

            await self.model.send(audio_data)

            mock_get_bytes.assert_called_once_with(
                "https://example.com/audio.wav",
            )

            self.mock_websocket.send.assert_called_once()

            sent_message = self.mock_websocket.send.call_args[0][0]
            sent_data = json.loads(sent_message)

            self.assertEqual(sent_data["type"], "input_audio_buffer.append")
            self.assertEqual(sent_data["audio"], "fetched_audio_bytes")
