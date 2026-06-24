# -*- coding: utf-8 -*-
"""Tests for DashScopeRealtimeModel.

Covers session config building, tool result encoding, tool-call argument
streaming (delta vs accumulated), name tracking via output_item events,
and parse_api_message for various WebSocket frame types.
"""
# pylint: disable=protected-access
import json
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import TextBlock, ToolResultBlock
from agentscope.realtime import ModelEvents, DashScopeRealtimeModel
from agentscope.credential import DashScopeCredential


def _make_model() -> "DashScopeRealtimeModel":
    return DashScopeRealtimeModel(
        model_name="qwen3-omni-flash-realtime",
        credential=DashScopeCredential(api_key="sk-test"),
    )


# ------------------------------------------------------------------ #
# Session config
# ------------------------------------------------------------------ #


class SessionConfigTest(IsolatedAsyncioTestCase):
    """DashScope session.update message generation."""

    async def test_config_includes_tools(self) -> None:
        """session.update config includes the provided tools list."""
        model = _make_model()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "Echo",
                    "description": "Echoes input",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                    },
                },
            },
        ]
        config = model._build_session_config("Hello", tools)
        self.assertEqual(config["type"], "session.update")
        self.assertEqual(config["session"]["tools"], tools)
        self.assertEqual(config["session"]["instructions"], "Hello")

    async def test_config_without_tools(self) -> None:
        """session.update config omits tools key when tools is None."""
        model = _make_model()
        config = model._build_session_config("Hello", None)
        self.assertNotIn("tools", config["session"])

    async def test_config_includes_voice_and_vad(self) -> None:
        """Config includes voice and server_vad turn_detection."""
        model = _make_model()
        config = model._build_session_config("Hi", None)
        session = config["session"]
        self.assertIn("voice", session)
        self.assertEqual(
            session["turn_detection"],
            {
                "type": "server_vad",
                "threshold": 0.5,
                "silence_duration_ms": 800,
            },
        )


# ------------------------------------------------------------------ #
# Tool result encoding
# ------------------------------------------------------------------ #


class ToolResultEncodingTest(IsolatedAsyncioTestCase):
    """DashScope _encode_tool_result output."""

    async def test_string_output(self) -> None:
        """_encode_tool_result returns valid JSON for string output."""
        block = ToolResultBlock(id="call_1", name="Echo", output="hello")
        payload = json.loads(DashScopeRealtimeModel._encode_tool_result(block))
        self.assertEqual(
            payload,
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "hello",
                },
            },
        )

    async def test_list_output_concatenates_text(self) -> None:
        """_encode_tool_result joins TextBlock list into a string."""
        block = ToolResultBlock(
            id="call_1",
            name="Echo",
            output=[TextBlock(text="part1"), TextBlock(text="part2")],
        )
        payload = json.loads(DashScopeRealtimeModel._encode_tool_result(block))
        self.assertEqual(
            payload,
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "part1part2",
                },
            },
        )


# ------------------------------------------------------------------ #
# Delta fragment (not accumulated)
# ------------------------------------------------------------------ #


class DeltaFragmentTest(IsolatedAsyncioTestCase):
    """Verify delta events carry only the incremental fragment."""

    async def test_delta_is_fragment_not_accumulated(self) -> None:
        """Delta event carries only the incremental fragment."""
        model = _make_model()
        model._response_id = "resp_1"
        model._tool_name_map["call_1"] = "Echo"

        evt1 = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "call_id": "call_1",
                    "item_id": "item_1",
                    "delta": '{"te',
                },
            ),
        )
        assert isinstance(evt1, ModelEvents.ModelResponseToolCallDeltaEvent)
        self.assertEqual(evt1.tool_call.input, '{"te')

        evt2 = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "call_id": "call_1",
                    "item_id": "item_1",
                    "delta": 'xt": "hi"}',
                },
            ),
        )
        assert isinstance(evt2, ModelEvents.ModelResponseToolCallDeltaEvent)
        self.assertEqual(evt2.tool_call.input, 'xt": "hi"}')

    async def test_done_carries_full_args(self) -> None:
        """Done event carries the authoritative full arguments string."""
        model = _make_model()
        model._response_id = "resp_1"
        model._tool_name_map["call_1"] = "Echo"

        await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "call_id": "call_1",
                    "item_id": "item_1",
                    "delta": '{"text": "hi"}',
                },
            ),
        )
        done = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.function_call_arguments.done",
                    "call_id": "call_1",
                    "item_id": "item_1",
                    "arguments": '{"text": "hi"}',
                },
            ),
        )
        assert isinstance(done, ModelEvents.ModelResponseToolCallDoneEvent)
        self.assertEqual(done.tool_call.input, '{"text": "hi"}')
        self.assertNotIn("call_1", model._tool_args_accumulator)


# ------------------------------------------------------------------ #
# Name tracking via output_item.added / output_item.done
# ------------------------------------------------------------------ #


class NameTrackingTest(IsolatedAsyncioTestCase):
    """DashScope populates _tool_name_map from output_item events."""

    async def test_output_item_added_records_name(self) -> None:
        """response.output_item.added populates _tool_name_map."""
        model = _make_model()
        result = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.output_item.added",
                    "item": {
                        "type": "function_call",
                        "call_id": "call_42",
                        "name": "Bash",
                    },
                },
            ),
        )
        self.assertIsNone(result)
        self.assertEqual(model._tool_name_map["call_42"], "Bash")

    async def test_output_item_done_records_name(self) -> None:
        """response.output_item.done populates _tool_name_map."""
        model = _make_model()
        result = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "call_43",
                        "name": "Write",
                    },
                },
            ),
        )
        self.assertIsNone(result)
        self.assertEqual(model._tool_name_map["call_43"], "Write")

    async def test_name_propagates_to_delta(self) -> None:
        """Tool name set via output_item.added appears in delta event."""
        model = _make_model()
        model._response_id = "resp_1"

        await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.output_item.added",
                    "item": {
                        "type": "function_call",
                        "call_id": "call_42",
                        "name": "Bash",
                    },
                },
            ),
        )
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "call_id": "call_42",
                    "item_id": "item_1",
                    "delta": '{"cmd": "ls"}',
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelResponseToolCallDeltaEvent)
        self.assertEqual(evt.tool_call.name, "Bash")

    async def test_name_propagates_to_done_and_cleans_up(self) -> None:
        """Tool name appears in done event and call_id is removed from map."""
        model = _make_model()
        model._response_id = "resp_1"

        await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.output_item.added",
                    "item": {
                        "type": "function_call",
                        "call_id": "call_42",
                        "name": "Write",
                    },
                },
            ),
        )
        await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "call_id": "call_42",
                    "item_id": "item_1",
                    "delta": '{"file": "test.txt"}',
                },
            ),
        )
        done = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.function_call_arguments.done",
                    "call_id": "call_42",
                    "item_id": "item_1",
                    "arguments": '{"file": "test.txt"}',
                },
            ),
        )
        assert isinstance(done, ModelEvents.ModelResponseToolCallDoneEvent)
        self.assertEqual(done.tool_call.name, "Write")
        self.assertNotIn("call_42", model._tool_name_map)

    async def test_non_function_call_item_ignored(self) -> None:
        """Non-function_call items leave _tool_name_map unchanged."""
        model = _make_model()
        await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.output_item.added",
                    "item": {"type": "message", "id": "item_1"},
                },
            ),
        )
        self.assertEqual(len(model._tool_name_map), 0)


# ------------------------------------------------------------------ #
# parse_api_message – various event types
# ------------------------------------------------------------------ #


class ParseApiMessageTest(IsolatedAsyncioTestCase):
    """DashScope parse_api_message for session, response, audio, VAD, etc."""

    async def test_session_created(self) -> None:
        """session.created yields ModelSessionCreatedEvent with session_id."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "session.created",
                    "session": {"id": "sess_abc"},
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelSessionCreatedEvent)
        self.assertEqual(evt.session_id, "sess_abc")

    async def test_response_created(self) -> None:
        """response.created event; also sets _response_id on the model."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.created",
                    "response": {"id": "resp_1"},
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelResponseCreatedEvent)
        self.assertEqual(evt.response_id, "resp_1")
        self.assertEqual(model._response_id, "resp_1")

    async def test_response_done(self) -> None:
        """response.done yields ModelResponseDoneEvent with token counts."""
        model = _make_model()
        model._response_id = "resp_1"
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.done",
                    "response": {
                        "id": "resp_1",
                        "usage": {"input_tokens": 5, "output_tokens": 15},
                    },
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelResponseDoneEvent)
        self.assertEqual(evt.input_tokens, 5)
        self.assertEqual(evt.output_tokens, 15)
        self.assertEqual(model._response_id, "")

    async def test_audio_delta(self) -> None:
        """response.audio.delta yields ModelResponseAudioDeltaEvent."""
        model = _make_model()
        model._response_id = "resp_1"
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.audio.delta",
                    "item_id": "item_1",
                    "delta": "base64audiodata",
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelResponseAudioDeltaEvent)
        self.assertEqual(evt.delta, "base64audiodata")

    async def test_audio_delta_empty_returns_none(self) -> None:
        """response.audio.delta with empty data returns None."""
        model = _make_model()
        model._response_id = "resp_1"
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.audio.delta",
                    "item_id": "item_1",
                    "delta": "",
                },
            ),
        )
        self.assertIsNone(evt)

    async def test_transcript_delta(self) -> None:
        """audio_transcript.delta yields AudioTranscriptDeltaEvent."""
        model = _make_model()
        model._response_id = "resp_1"
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.audio_transcript.delta",
                    "item_id": "item_1",
                    "delta": "hello",
                },
            ),
        )
        assert isinstance(
            evt,
            ModelEvents.ModelResponseAudioTranscriptDeltaEvent,
        )
        self.assertEqual(evt.delta, "hello")

    async def test_input_transcription_completed(self) -> None:
        """input_audio_transcription.completed yields done event."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription."
                    "completed",
                    "item_id": "item_1",
                    "transcript": "user said this",
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelInputTranscriptionDoneEvent)
        self.assertEqual(evt.transcript, "user said this")

    async def test_vad_speech_started(self) -> None:
        """input_audio_buffer.speech_started yields ModelInputStartedEvent."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "input_audio_buffer.speech_started",
                    "item_id": "item_1",
                    "audio_start_ms": 1000,
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelInputStartedEvent)
        self.assertEqual(evt.audio_start_ms, 1000)

    async def test_vad_speech_stopped(self) -> None:
        """input_audio_buffer.speech_stopped yields ModelInputDoneEvent."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "input_audio_buffer.speech_stopped",
                    "item_id": "item_1",
                    "audio_end_ms": 2000,
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelInputDoneEvent)
        self.assertEqual(evt.audio_end_ms, 2000)

    async def test_error_event(self) -> None:
        """error frame yields ModelErrorEvent with code and message."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "type": "server_error",
                        "code": "500",
                        "message": "Internal error",
                    },
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelErrorEvent)
        self.assertEqual(evt.code, "500")

    async def test_invalid_json(self) -> None:
        """Non-JSON string returns None without raising."""
        model = _make_model()
        self.assertIsNone(await model.parse_api_message("not json"))

    async def test_unhandled_event(self) -> None:
        """Unknown event type returns None."""
        model = _make_model()
        self.assertIsNone(
            await model.parse_api_message(
                json.dumps({"type": "unknown.event"}),
            ),
        )

    async def test_session_updated_returns_none(self) -> None:
        """session.updated event returns None."""
        model = _make_model()
        self.assertIsNone(
            await model.parse_api_message(
                json.dumps({"type": "session.updated"}),
            ),
        )
