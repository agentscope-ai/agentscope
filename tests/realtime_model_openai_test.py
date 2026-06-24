# -*- coding: utf-8 -*-
"""Tests for OpenAIRealtimeModel.

Covers session config building, tool schema formatting, tool result
encoding, tool-call argument streaming (delta fragment), and
parse_api_message for various WebSocket frame types.
"""
# pylint: disable=protected-access
import json
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import TextBlock, ToolResultBlock
from agentscope.realtime import ModelEvents, OpenAIRealtimeModel
from agentscope.credential import OpenAICredential


def _make_model() -> "OpenAIRealtimeModel":
    return OpenAIRealtimeModel(
        model_name="gpt-realtime-1.5",
        credential=OpenAICredential(api_key="sk-test"),
    )


# ------------------------------------------------------------------ #
# Session config
# ------------------------------------------------------------------ #


class SessionConfigTest(IsolatedAsyncioTestCase):
    """OpenAI session.update message generation."""

    async def test_config_includes_flattened_tools(self) -> None:
        """session.update config contains flattened tool entries."""
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
        self.assertEqual(
            config["session"]["tools"],
            [
                {
                    "type": "function",
                    "name": "Echo",
                    "description": "Echoes input",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                    },
                },
            ],
        )

    async def test_config_without_tools(self) -> None:
        """session.update config omits tools key when tools is None."""
        model = _make_model()
        config = model._build_session_config("Hello", None)
        self.assertNotIn("tools", config["session"])

    async def test_config_includes_transcription_when_enabled(self) -> None:
        """session.update config includes transcription model and language."""
        model = _make_model()
        config = model._build_session_config("Hi", None)
        self.assertEqual(
            config["session"]["audio"]["input"]["transcription"],
            {"model": "gpt-realtime-whisper", "language": "zh"},
        )

    async def test_config_voice(self) -> None:
        """session.update config contains the alloy voice setting."""
        model = _make_model()
        config = model._build_session_config("Hi", None)
        self.assertEqual(
            config["session"]["audio"]["output"],
            {"voice": "alloy"},
        )


# ------------------------------------------------------------------ #
# Tool schema flattening
# ------------------------------------------------------------------ #


class FormatToolkitSchemaTest(IsolatedAsyncioTestCase):
    """Verify _format_toolkit_schema flattens Chat Completions shape."""

    async def test_single_tool(self) -> None:
        """_format_toolkit_schema flattens a single Chat Completions tool."""
        schemas = [
            {
                "type": "function",
                "function": {
                    "name": "Bash",
                    "description": "Run bash",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
        result = OpenAIRealtimeModel._format_toolkit_schema(schemas)
        self.assertEqual(
            result,
            [
                {
                    "type": "function",
                    "name": "Bash",
                    "description": "Run bash",
                    "parameters": {"type": "object", "properties": {}},
                },
            ],
        )

    async def test_multiple_tools(self) -> None:
        """_format_toolkit_schema flattens multiple Chat Completions tools."""
        schemas = [
            {"type": "function", "function": {"name": "A"}},
            {"type": "function", "function": {"name": "B"}},
        ]
        result = OpenAIRealtimeModel._format_toolkit_schema(schemas)
        self.assertEqual(
            result,
            [
                {"type": "function", "name": "A"},
                {"type": "function", "name": "B"},
            ],
        )


# ------------------------------------------------------------------ #
# Tool result encoding
# ------------------------------------------------------------------ #


class ToolResultEncodingTest(IsolatedAsyncioTestCase):
    """OpenAI _encode_tool_result output."""

    async def test_string_output(self) -> None:
        """_encode_tool_result returns valid JSON for string output."""
        block = ToolResultBlock(id="call_1", name="Echo", output="hello")
        payload = json.loads(OpenAIRealtimeModel._encode_tool_result(block))
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
            output=[TextBlock(text="a"), TextBlock(text="b")],
        )
        payload = json.loads(OpenAIRealtimeModel._encode_tool_result(block))
        self.assertEqual(
            payload,
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "ab",
                },
            },
        )


# ------------------------------------------------------------------ #
# Delta fragment (not accumulated)
# ------------------------------------------------------------------ #


class DeltaFragmentTest(IsolatedAsyncioTestCase):
    """Verify delta events carry only the incremental fragment."""

    async def test_delta_is_fragment_not_accumulated(self) -> None:
        """Delta event carries only the incremental fragment, not full args."""
        model = _make_model()
        model._response_id = "resp_1"

        evt1 = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "call_id": "call_1",
                    "name": "Echo",
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
                    "name": "Echo",
                    "item_id": "item_1",
                    "delta": 'xt": "hi"}',
                },
            ),
        )
        assert isinstance(evt2, ModelEvents.ModelResponseToolCallDeltaEvent)
        self.assertEqual(evt2.tool_call.input, 'xt": "hi"}')

    async def test_accumulator_used_for_done(self) -> None:
        """Done event uses the authoritative arguments, clears accumulator."""
        model = _make_model()
        model._response_id = "resp_1"

        await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "call_id": "call_1",
                    "name": "Echo",
                    "item_id": "item_1",
                    "delta": '{"te',
                },
            ),
        )
        await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "call_id": "call_1",
                    "name": "Echo",
                    "item_id": "item_1",
                    "delta": 'xt": "hi"}',
                },
            ),
        )
        done = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.function_call_arguments.done",
                    "call_id": "call_1",
                    "name": "Echo",
                    "item_id": "item_1",
                    "arguments": '{"text": "hi"}',
                },
            ),
        )
        assert isinstance(done, ModelEvents.ModelResponseToolCallDoneEvent)
        self.assertEqual(done.tool_call.input, '{"text": "hi"}')
        self.assertNotIn("call_1", model._tool_args_accumulator)


# ------------------------------------------------------------------ #
# parse_api_message – various event types
# ------------------------------------------------------------------ #


class ParseApiMessageTest(IsolatedAsyncioTestCase):
    """OpenAI parse_api_message for session, response, audio, VAD, etc."""

    async def test_session_created(self) -> None:
        """session.created yields ModelSessionCreatedEvent with session_id."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "session.created",
                    "session": {"id": "sess_123"},
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelSessionCreatedEvent)
        self.assertEqual(evt.session_id, "sess_123")

    async def test_session_updated_returns_none(self) -> None:
        """session.updated event returns None."""
        model = _make_model()
        self.assertIsNone(
            await model.parse_api_message(
                json.dumps({"type": "session.updated"}),
            ),
        )

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
                        "usage": {"input_tokens": 10, "output_tokens": 20},
                    },
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelResponseDoneEvent)
        self.assertEqual(evt.input_tokens, 10)
        self.assertEqual(evt.output_tokens, 20)
        self.assertEqual(model._response_id, "")

    async def test_audio_delta(self) -> None:
        """response.output_audio.delta yields ModelResponseAudioDeltaEvent."""
        model = _make_model()
        model._response_id = "resp_1"
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.output_audio.delta",
                    "item_id": "item_1",
                    "delta": "base64audiodata",
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelResponseAudioDeltaEvent)
        self.assertEqual(evt.delta, "base64audiodata")
        self.assertEqual(evt.format.rate, 24000)

    async def test_audio_delta_empty_returns_none(self) -> None:
        """response.output_audio.delta with empty data returns None."""
        model = _make_model()
        model._response_id = "resp_1"
        self.assertIsNone(
            await model.parse_api_message(
                json.dumps(
                    {
                        "type": "response.output_audio.delta",
                        "item_id": "item_1",
                        "delta": "",
                    },
                ),
            ),
        )

    async def test_audio_done(self) -> None:
        """response.output_audio.done yields ModelResponseAudioDoneEvent."""
        model = _make_model()
        model._response_id = "resp_1"
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.output_audio.done",
                    "item_id": "item_1",
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelResponseAudioDoneEvent)

    async def test_transcript_delta(self) -> None:
        """output_audio_transcript.delta yields transcript delta."""
        model = _make_model()
        model._response_id = "resp_1"
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.output_audio_transcript.delta",
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

    async def test_transcript_done(self) -> None:
        """output_audio_transcript.done yields transcript done."""
        model = _make_model()
        model._response_id = "resp_1"
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "response.output_audio_transcript.done",
                    "item_id": "item_1",
                },
            ),
        )
        assert isinstance(
            evt,
            ModelEvents.ModelResponseAudioTranscriptDoneEvent,
        )

    async def test_input_transcription_delta(self) -> None:
        """input_audio_transcription.delta yields delta event."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription."
                    "delta",
                    "item_id": "item_1",
                    "delta": "user said",
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelInputTranscriptionDeltaEvent)
        self.assertEqual(evt.delta, "user said")

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
                    "audio_start_ms": 500,
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelInputStartedEvent)
        self.assertEqual(evt.audio_start_ms, 500)

    async def test_vad_speech_stopped(self) -> None:
        """input_audio_buffer.speech_stopped yields ModelInputDoneEvent."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "input_audio_buffer.speech_stopped",
                    "item_id": "item_1",
                    "audio_end_ms": 1500,
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelInputDoneEvent)
        self.assertEqual(evt.audio_end_ms, 1500)

    async def test_error_event(self) -> None:
        """error frame yields ModelErrorEvent with type, code, and message."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "type": "invalid_request",
                        "code": "bad_tool",
                        "message": "Tool not found",
                    },
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelErrorEvent)
        self.assertEqual(evt.error_type, "invalid_request")
        self.assertEqual(evt.code, "bad_tool")
        self.assertEqual(evt.message, "Tool not found")

    async def test_invalid_json(self) -> None:
        """Non-JSON string returns None without raising."""
        model = _make_model()
        self.assertIsNone(await model.parse_api_message("not json{"))

    async def test_unhandled_event(self) -> None:
        """Unknown event type returns None."""
        model = _make_model()
        self.assertIsNone(
            await model.parse_api_message(
                json.dumps({"type": "rate_limits.updated", "rate_limits": []}),
            ),
        )
