# -*- coding: utf-8 -*-
"""The realtime event test unittests."""
from unittest import IsolatedAsyncioTestCase

from agentscope.realtime import ModelEvents, ServerEvents, ClientEvents
from agentscope.message import ToolUseBlock


class TestServerEventsFromModelEvent(IsolatedAsyncioTestCase):
    """Test ServerEvents.from_model_event method."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.agent_id = "test_agent_123"
        self.agent_name = "TestAgent"

    async def test_model_response_created_event(self) -> None:
        """Test converting ModelResponseCreatedEvent to
        AgentResponseCreatedEvent."""
        model_event = ModelEvents.ModelResponseCreatedEvent(
            response_id="resp_001",
        )

        server_event = ServerEvents.from_model_event(
            model_event,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )

        self.assertIsInstance(
            server_event,
            ServerEvents.AgentResponseCreatedEvent,
        )
        self.assertEqual(server_event.response_id, "resp_001")
        self.assertEqual(server_event.agent_id, self.agent_id)
        self.assertEqual(server_event.agent_name, self.agent_name)
        self.assertEqual(server_event.type, "agent_response_created")

    async def test_model_response_done_event(self) -> None:
        """Test converting ModelResponseDoneEvent to AgentResponseDoneEvent."""
        model_event = ModelEvents.ModelResponseDoneEvent(
            response_id="resp_002",
            input_tokens=100,
            output_tokens=50,
            metadata={"key": "value"},
        )

        server_event = ServerEvents.from_model_event(
            model_event,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )

        self.assertIsInstance(
            server_event,
            ServerEvents.AgentResponseDoneEvent,
        )
        self.assertEqual(server_event.response_id, "resp_002")
        self.assertEqual(server_event.input_tokens, 100)
        self.assertEqual(server_event.output_tokens, 50)
        self.assertEqual(server_event.metadata, {"key": "value"})
        self.assertEqual(server_event.agent_id, self.agent_id)
        self.assertEqual(server_event.agent_name, self.agent_name)
        self.assertEqual(server_event.type, "agent_response_done")

    async def test_model_response_audio_delta_event(self) -> None:
        """Test converting ModelResponseAudioDeltaEvent to
        AgentResponseAudioDeltaEvent."""
        model_event = ModelEvents.ModelResponseAudioDeltaEvent(
            response_id="resp_003",
            item_id="item_001",
            delta="base64_audio_data",
            format={"type": "audio/pcm", "rate": 16000},
        )

        server_event = ServerEvents.from_model_event(
            model_event,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )

        self.assertIsInstance(
            server_event,
            ServerEvents.AgentResponseAudioDeltaEvent,
        )
        self.assertEqual(server_event.response_id, "resp_003")
        self.assertEqual(server_event.item_id, "item_001")
        self.assertEqual(server_event.delta, "base64_audio_data")
        self.assertEqual(server_event.format.type, "audio/pcm")
        self.assertEqual(server_event.format.rate, 16000)
        self.assertEqual(server_event.agent_id, self.agent_id)
        self.assertEqual(server_event.agent_name, self.agent_name)

    async def test_model_response_audio_done_event(self) -> None:
        """Test converting ModelResponseAudioDoneEvent to
        AgentResponseAudioDoneEvent."""
        model_event = ModelEvents.ModelResponseAudioDoneEvent(
            response_id="resp_004",
            item_id="item_002",
        )

        server_event = ServerEvents.from_model_event(
            model_event,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )

        self.assertIsInstance(
            server_event,
            ServerEvents.AgentResponseAudioDoneEvent,
        )
        self.assertEqual(server_event.response_id, "resp_004")
        self.assertEqual(server_event.item_id, "item_002")
        self.assertEqual(server_event.agent_id, self.agent_id)
        self.assertEqual(server_event.agent_name, self.agent_name)

    async def test_model_response_audio_transcript_delta_event(self) -> None:
        """Test converting ModelResponseAudioTranscriptDeltaEvent."""
        model_event = ModelEvents.ModelResponseAudioTranscriptDeltaEvent(
            response_id="resp_005",
            item_id="item_003",
            delta="Hello ",
        )

        server_event = ServerEvents.from_model_event(
            model_event,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )

        self.assertIsInstance(
            server_event,
            ServerEvents.AgentResponseAudioTranscriptDeltaEvent,
        )
        self.assertEqual(server_event.response_id, "resp_005")
        self.assertEqual(server_event.item_id, "item_003")
        self.assertEqual(server_event.delta, "Hello ")
        self.assertEqual(server_event.agent_id, self.agent_id)
        self.assertEqual(server_event.agent_name, self.agent_name)

    async def test_model_response_audio_transcript_done_event(self) -> None:
        """Test converting ModelResponseAudioTranscriptDoneEvent."""
        model_event = ModelEvents.ModelResponseAudioTranscriptDoneEvent(
            response_id="resp_006",
            item_id="item_004",
        )

        server_event = ServerEvents.from_model_event(
            model_event,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )

        self.assertIsInstance(
            server_event,
            ServerEvents.AgentResponseAudioTranscriptDoneEvent,
        )
        self.assertEqual(server_event.response_id, "resp_006")
        self.assertEqual(server_event.item_id, "item_004")
        self.assertEqual(server_event.agent_id, self.agent_id)
        self.assertEqual(server_event.agent_name, self.agent_name)

    async def test_model_response_tool_use_delta_event(self) -> None:
        """Test converting ModelResponseToolUseDeltaEvent."""
        tool_use = ToolUseBlock(
            type="tool_use",
            id="tool_001",
            name="get_weather",
            input={"location": "San"},
        )

        model_event = ModelEvents.ModelResponseToolUseDeltaEvent(
            response_id="resp_007",
            item_id="item_005",
            tool_use=tool_use,
        )

        server_event = ServerEvents.from_model_event(
            model_event,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )

        self.assertIsInstance(
            server_event,
            ServerEvents.AgentResponseToolUseDeltaEvent,
        )
        self.assertEqual(server_event.response_id, "resp_007")
        self.assertEqual(server_event.item_id, "item_005")
        self.assertEqual(server_event.tool_use["id"], "tool_001")
        self.assertEqual(server_event.tool_use["name"], "get_weather")
        self.assertEqual(server_event.agent_id, self.agent_id)
        self.assertEqual(server_event.agent_name, self.agent_name)

    async def test_model_response_tool_use_done_event(self) -> None:
        """Test converting ModelResponseToolUseDoneEvent."""
        tool_use = ToolUseBlock(
            type="tool_use",
            id="tool_002",
            name="get_weather",
            input={"location": "San Francisco"},
        )

        model_event = ModelEvents.ModelResponseToolUseDoneEvent(
            response_id="resp_008",
            item_id="item_006",
            tool_use=tool_use,
        )

        server_event = ServerEvents.from_model_event(
            model_event,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )

        self.assertIsInstance(
            server_event,
            ServerEvents.AgentResponseToolUseDoneEvent,
        )
        self.assertEqual(server_event.response_id, "resp_008")
        self.assertEqual(server_event.item_id, "item_006")
        self.assertEqual(server_event.tool_use["id"], "tool_002")
        self.assertEqual(server_event.tool_use["name"], "get_weather")
        self.assertEqual(server_event.agent_id, self.agent_id)
        self.assertEqual(server_event.agent_name, self.agent_name)

    async def test_model_input_transcription_delta_event(self) -> None:
        """Test converting ModelInputTranscriptionDeltaEvent."""
        model_event = ModelEvents.ModelInputTranscriptionDeltaEvent(
            item_id="item_007",
            delta="How are ",
        )

        server_event = ServerEvents.from_model_event(
            model_event,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )

        self.assertIsInstance(
            server_event,
            ServerEvents.AgentInputTranscriptionDeltaEvent,
        )
        self.assertEqual(server_event.item_id, "item_007")
        self.assertEqual(server_event.delta, "How are ")
        self.assertEqual(server_event.agent_id, self.agent_id)
        self.assertEqual(server_event.agent_name, self.agent_name)

    async def test_model_input_transcription_done_event(self) -> None:
        """Test converting ModelInputTranscriptionDoneEvent."""
        model_event = ModelEvents.ModelInputTranscriptionDoneEvent(
            transcript="How are you?",
            item_id="item_008",
            input_tokens=10,
            output_tokens=5,
        )

        server_event = ServerEvents.from_model_event(
            model_event,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )

        self.assertIsInstance(
            server_event,
            ServerEvents.AgentInputTranscriptionDoneEvent,
        )
        self.assertEqual(server_event.transcript, "How are you?")
        self.assertEqual(server_event.item_id, "item_008")
        self.assertEqual(server_event.input_tokens, 10)
        self.assertEqual(server_event.output_tokens, 5)
        self.assertEqual(server_event.agent_id, self.agent_id)
        self.assertEqual(server_event.agent_name, self.agent_name)

    async def test_model_input_started_event(self) -> None:
        """Test converting ModelInputStartedEvent."""
        model_event = ModelEvents.ModelInputStartedEvent(
            item_id="item_009",
            audio_start_ms=1000,
        )

        server_event = ServerEvents.from_model_event(
            model_event,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )

        self.assertIsInstance(
            server_event,
            ServerEvents.AgentInputStartedEvent,
        )
        self.assertEqual(server_event.item_id, "item_009")
        self.assertEqual(server_event.audio_start_ms, 1000)
        self.assertEqual(server_event.agent_id, self.agent_id)
        self.assertEqual(server_event.agent_name, self.agent_name)

    async def test_model_input_done_event(self) -> None:
        """Test converting ModelInputDoneEvent."""
        model_event = ModelEvents.ModelInputDoneEvent(
            item_id="item_010",
            audio_end_ms=5000,
        )

        server_event = ServerEvents.from_model_event(
            model_event,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )

        self.assertIsInstance(
            server_event,
            ServerEvents.AgentInputDoneEvent,
        )
        self.assertEqual(server_event.item_id, "item_010")
        self.assertEqual(server_event.audio_end_ms, 5000)
        self.assertEqual(server_event.agent_id, self.agent_id)
        self.assertEqual(server_event.agent_name, self.agent_name)

    async def test_model_error_event(self) -> None:
        """Test converting ModelErrorEvent."""
        model_event = ModelEvents.ModelErrorEvent(
            error_type="rate_limit_error",
            code="429",
            message="Rate limit exceeded",
        )

        server_event = ServerEvents.from_model_event(
            model_event,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )

        self.assertIsInstance(
            server_event,
            ServerEvents.AgentErrorEvent,
        )
        self.assertEqual(server_event.error_type, "rate_limit_error")
        self.assertEqual(server_event.code, "429")
        self.assertEqual(server_event.message, "Rate limit exceeded")
        self.assertEqual(server_event.agent_id, self.agent_id)
        self.assertEqual(server_event.agent_name, self.agent_name)
        self.assertEqual(server_event.type, "agent_error")


class TestClientEventsFromJson(IsolatedAsyncioTestCase):
    """Test ClientEvents.from_json method."""

    async def test_client_session_create_event(self) -> None:
        """Test parsing ClientSessionCreateEvent from JSON."""
        json_data = {
            "type": "client_session_create",
            "config": {
                "instructions": "You are a helpful assistant.",
                "user_name": "TestUser",
            },
        }

        event = ClientEvents.from_json(json_data)

        self.assertIsInstance(event, ClientEvents.ClientSessionCreateEvent)
        self.assertEqual(event.type, "client_session_create")
        self.assertEqual(
            event.config["instructions"],
            "You are a helpful assistant.",
        )
        self.assertEqual(event.config["user_name"], "TestUser")

    async def test_client_session_end_event(self) -> None:
        """Test parsing ClientSessionEndEvent from JSON."""
        json_data = {
            "type": "client_session_end",
            "session_id": "session_123",
        }

        event = ClientEvents.from_json(json_data)

        self.assertIsInstance(event, ClientEvents.ClientSessionEndEvent)
        self.assertEqual(event.type, "client_session_end")
        self.assertEqual(event.session_id, "session_123")

    async def test_client_response_create_event(self) -> None:
        """Test parsing ClientResponseCreateEvent from JSON."""
        json_data = {
            "type": "client_response_create",
            "session_id": "session_456",
        }

        event = ClientEvents.from_json(json_data)

        self.assertIsInstance(event, ClientEvents.ClientResponseCreateEvent)
        self.assertEqual(event.type, "client_response_create")
        self.assertEqual(event.session_id, "session_456")

    async def test_client_response_cancel_event(self) -> None:
        """Test parsing ClientResponseCancelEvent from JSON."""
        json_data = {
            "type": "client_response_cancel",
            "session_id": "session_789",
        }

        event = ClientEvents.from_json(json_data)

        self.assertIsInstance(event, ClientEvents.ClientResponseCancelEvent)
        self.assertEqual(event.type, "client_response_cancel")
        self.assertEqual(event.session_id, "session_789")

    async def test_client_image_append_event(self) -> None:
        """Test parsing ClientImageAppendEvent from JSON."""
        json_data = {
            "type": "client_image_append",
            "session_id": "session_001",
            "image": "base64_image_data",
            "format": {"mime_type": "image/png"},
        }

        event = ClientEvents.from_json(json_data)

        self.assertIsInstance(event, ClientEvents.ClientImageAppendEvent)
        self.assertEqual(event.type, "client_image_append")
        self.assertEqual(event.session_id, "session_001")
        self.assertEqual(event.image, "base64_image_data")
        self.assertEqual(event.format, {"mime_type": "image/png"})

    async def test_client_text_append_event(self) -> None:
        """Test parsing ClientTextAppendEvent from JSON."""
        json_data = {
            "type": "client_text_append",
            "session_id": "session_002",
            "text": "Hello, how are you?",
        }

        event = ClientEvents.from_json(json_data)

        self.assertIsInstance(event, ClientEvents.ClientTextAppendEvent)
        self.assertEqual(event.type, "client_text_append")
        self.assertEqual(event.session_id, "session_002")
        self.assertEqual(event.text, "Hello, how are you?")

    async def test_client_audio_append_event(self) -> None:
        """Test parsing ClientAudioAppendEvent from JSON."""
        json_data = {
            "type": "client_audio_append",
            "session_id": "session_003",
            "audio": "base64_audio_data",
            "format": {"type": "audio/pcm", "rate": 16000},
        }

        event = ClientEvents.from_json(json_data)

        self.assertIsInstance(event, ClientEvents.ClientAudioAppendEvent)
        self.assertEqual(event.type, "client_audio_append")
        self.assertEqual(event.session_id, "session_003")
        self.assertEqual(event.audio, "base64_audio_data")
        self.assertEqual(event.format.type, "audio/pcm")
        self.assertEqual(event.format.rate, 16000)

    async def test_client_audio_commit_event(self) -> None:
        """Test parsing ClientAudioCommitEvent from JSON."""
        json_data = {
            "type": "client_audio_commit",
            "session_id": "session_004",
        }

        event = ClientEvents.from_json(json_data)

        self.assertIsInstance(event, ClientEvents.ClientAudioCommitEvent)
        self.assertEqual(event.type, "client_audio_commit")
        self.assertEqual(event.session_id, "session_004")

    async def test_client_tool_result_event(self) -> None:
        """Test parsing ClientToolResultEvent from JSON."""
        json_data = {
            "type": "client_tool_result",
            "session_id": "session_005",
            "id": "tool_call_123",
            "name": "get_weather",
            "output": "The weather is sunny, 25°C",
        }

        event = ClientEvents.from_json(json_data)

        self.assertIsInstance(event, ClientEvents.ClientToolResultEvent)
        self.assertEqual(event.type, "client_tool_result")
        self.assertEqual(event.session_id, "session_005")
        self.assertEqual(event.id, "tool_call_123")
        self.assertEqual(event.name, "get_weather")
        self.assertEqual(event.output, "The weather is sunny, 25°C")

    async def test_invalid_json_data_no_type(self) -> None:
        """Test parsing invalid JSON data without type field."""
        json_data = {
            "session_id": "session_006",
        }

        with self.assertRaises(ValueError) as context:
            ClientEvents.from_json(json_data)

        self.assertIn("Invalid JSON data", str(context.exception))

    async def test_invalid_json_data_not_dict(self) -> None:
        """Test parsing invalid JSON data that is not a dict."""
        json_data = "not a dict"

        with self.assertRaises(ValueError) as context:
            ClientEvents.from_json(json_data)

        self.assertIn("Invalid JSON data", str(context.exception))

    async def test_unknown_event_type(self) -> None:
        """Test parsing JSON with unknown event type."""
        json_data = {
            "type": "unknown_event_type",
            "session_id": "session_007",
        }

        with self.assertRaises(ValueError) as context:
            ClientEvents.from_json(json_data)

        self.assertIn("Unknown ClientEvent type", str(context.exception))
