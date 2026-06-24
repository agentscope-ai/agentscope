# -*- coding: utf-8 -*-
"""Tests for GeminiRealtimeModel.

Covers session config building, tool schema formatting, tool result
encoding, and parse_api_message for various WebSocket frame types.

Key differences from OpenAI/DashScope models:
- Session config uses a ``setup`` message (not ``session.update``).
- Response IDs are generated client-side (no server ``response.created``).
- Tool calls arrive as done events only – there are no streaming delta
  events for arguments.
- Tool results use the ``toolResponse.functionResponses`` protocol.
"""
# pylint: disable=protected-access
import json
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import TextBlock, ToolResultBlock
from agentscope.realtime import ModelEvents, GeminiRealtimeModel
from agentscope.credential import GeminiCredential


def _make_model() -> "GeminiRealtimeModel":
    return GeminiRealtimeModel(
        model_name="gemini-2.5-flash-native-audio-preview-12-2025",
        credential=GeminiCredential(api_key="fake-api-key"),
    )


# ------------------------------------------------------------------ #
# Session config
# ------------------------------------------------------------------ #


class SessionConfigTest(IsolatedAsyncioTestCase):
    """Gemini setup message generation."""

    async def test_config_uses_setup_key(self) -> None:
        """_build_session_config returns a dict with top-level ``setup``
        key."""
        model = _make_model()
        config = model._build_session_config("Hello", None)
        self.assertIn("setup", config)
        self.assertNotIn("type", config)

    async def test_config_includes_model_name(self) -> None:
        """setup.model contains the model name prefixed with ``models/``."""
        model = _make_model()
        config = model._build_session_config("Hi", None)
        self.assertEqual(
            config["setup"]["model"],
            "models/gemini-2.5-flash-native-audio-preview-12-2025",
        )

    async def test_config_includes_system_instruction(self) -> None:
        """setup.systemInstruction.parts[0].text contains the instructions."""
        model = _make_model()
        config = model._build_session_config("Be helpful", None)
        self.assertEqual(
            config["setup"]["systemInstruction"],
            {"parts": [{"text": "Be helpful"}]},
        )

    async def test_config_includes_tools(self) -> None:
        """setup.tools contains function_declarations when tools are given."""
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
        config = model._build_session_config("Hi", tools)
        self.assertEqual(
            config["setup"]["tools"],
            [
                {
                    "function_declarations": [
                        {
                            "name": "Echo",
                            "description": "Echoes input",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string"},
                                },
                            },
                        },
                    ],
                },
            ],
        )

    async def test_config_without_tools_omits_tools_key(self) -> None:
        """setup omits the ``tools`` key when tools is None."""
        model = _make_model()
        config = model._build_session_config("Hi", None)
        self.assertNotIn("tools", config["setup"])

    async def test_config_includes_voice(self) -> None:
        """setup.generationConfig.speechConfig contains the configured
        voice."""
        model = _make_model()
        config = model._build_session_config("Hi", None)
        self.assertEqual(
            config["setup"]["generationConfig"]["speechConfig"],
            {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": "Puck"},
                },
            },
        )

    async def test_config_includes_output_transcription_at_setup_level(
        self,
    ) -> None:
        """outputAudioTranscription is a top-level setup field."""
        model = _make_model()
        config = model._build_session_config("Hi", None)
        self.assertIn("outputAudioTranscription", config["setup"])
        self.assertNotIn(
            "outputAudioTranscription",
            config["setup"]["generationConfig"],
        )

    async def test_config_includes_input_transcription_at_setup_level(
        self,
    ) -> None:
        """inputAudioTranscription is a top-level setup field when enabled."""
        model = _make_model()
        config = model._build_session_config("Hi", None)
        self.assertIn("inputAudioTranscription", config["setup"])
        self.assertNotIn(
            "inputAudioTranscription",
            config["setup"]["generationConfig"],
        )

    async def test_config_omits_input_transcription_when_disabled(
        self,
    ) -> None:
        """inputAudioTranscription is absent when the flag is disabled."""
        model = GeminiRealtimeModel(
            model_name="gemini-2.5-flash-native-audio-preview-12-2025",
            credential=GeminiCredential(api_key="fake-api-key"),
            parameters=GeminiRealtimeModel.Parameters(
                enable_input_audio_transcription=False,
            ),
        )
        config = model._build_session_config("Hi", None)
        self.assertNotIn("inputAudioTranscription", config["setup"])

    async def test_config_includes_context_compression_by_default(
        self,
    ) -> None:
        """contextWindowCompression is a top-level setup field by default."""
        model = _make_model()
        config = model._build_session_config("Hi", None)
        self.assertIn("contextWindowCompression", config["setup"])
        self.assertNotIn(
            "contextWindowCompression",
            config["setup"]["generationConfig"],
        )

    async def test_config_session_resumption_empty_by_default(self) -> None:
        """sessionResumption is an empty dict when no handle is provided."""
        model = _make_model()
        config = model._build_session_config("Hi", None)
        self.assertEqual(config["setup"]["sessionResumption"], {})

    async def test_config_session_resumption_with_handle(self) -> None:
        """sessionResumption.handle is set when session_handle is provided."""
        model = _make_model()
        config = model._build_session_config(
            "Hi",
            None,
            session_handle="handle-abc",
        )
        self.assertEqual(
            config["setup"]["sessionResumption"],
            {"handle": "handle-abc"},
        )


# ------------------------------------------------------------------ #
# Tool schema formatting
# ------------------------------------------------------------------ #


class FormatToolkitSchemaTest(IsolatedAsyncioTestCase):
    """Verify _format_toolkit_schema converts to Gemini
    function_declarations."""

    async def test_single_tool(self) -> None:
        """_format_toolkit_schema wraps one tool into function_declarations."""
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
        result = GeminiRealtimeModel._format_toolkit_schema(schemas)
        self.assertEqual(
            result,
            [
                {
                    "function_declarations": [
                        {
                            "name": "Bash",
                            "description": "Run bash",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                            },
                        },
                    ],
                },
            ],
        )

    async def test_multiple_tools(self) -> None:
        """_format_toolkit_schema collects multiple tools into one list."""
        schemas = [
            {"type": "function", "function": {"name": "A"}},
            {"type": "function", "function": {"name": "B"}},
        ]
        result = GeminiRealtimeModel._format_toolkit_schema(schemas)
        self.assertEqual(
            result,
            [{"function_declarations": [{"name": "A"}, {"name": "B"}]}],
        )

    async def test_entry_without_function_key_is_skipped(self) -> None:
        """Entries without a ``function`` key are silently skipped."""
        schemas = [
            {"type": "other", "name": "NoFunction"},
            {"type": "function", "function": {"name": "Keep"}},
        ]
        result = GeminiRealtimeModel._format_toolkit_schema(schemas)
        self.assertEqual(
            result,
            [{"function_declarations": [{"name": "Keep"}]}],
        )


# ------------------------------------------------------------------ #
# Tool result encoding
# ------------------------------------------------------------------ #


class ToolResultEncodingTest(IsolatedAsyncioTestCase):
    """GeminiRealtimeModel._encode_tool_result output."""

    async def test_string_output_non_json_is_wrapped(self) -> None:
        """Plain-string output is wrapped as {\"result\": <text>}."""
        block = ToolResultBlock(id="call_1", name="Echo", output="hello")
        payload = json.loads(GeminiRealtimeModel._encode_tool_result(block))
        self.assertEqual(
            payload["toolResponse"]["functionResponses"][0],
            {"id": "call_1", "name": "Echo", "response": {"result": "hello"}},
        )

    async def test_string_output_valid_json_is_parsed(self) -> None:
        """JSON-string output is parsed and stored as a dict directly."""
        block = ToolResultBlock(
            id="call_2",
            name="Search",
            output='{"items": [1, 2, 3]}',
        )
        payload = json.loads(GeminiRealtimeModel._encode_tool_result(block))
        func_resp = payload["toolResponse"]["functionResponses"][0]
        self.assertEqual(func_resp["response"], {"items": [1, 2, 3]})

    async def test_list_output_concatenates_text_blocks(self) -> None:
        """TextBlock list is concatenated and wrapped as {\"result\": ...}."""
        block = ToolResultBlock(
            id="call_3",
            name="Echo",
            output=[TextBlock(text="part1"), TextBlock(text="part2")],
        )
        payload = json.loads(GeminiRealtimeModel._encode_tool_result(block))
        func_resp = payload["toolResponse"]["functionResponses"][0]
        self.assertEqual(func_resp["response"], {"result": "part1part2"})

    async def test_payload_structure(self) -> None:
        """Payload has the expected toolResponse / functionResponses
        nesting."""
        block = ToolResultBlock(id="c1", name="Fn", output="ok")
        payload = json.loads(GeminiRealtimeModel._encode_tool_result(block))
        self.assertEqual(
            payload,
            {
                "toolResponse": {
                    "functionResponses": [
                        {
                            "id": "c1",
                            "name": "Fn",
                            "response": {"result": "ok"},
                        },
                    ],
                },
            },
        )


# ------------------------------------------------------------------ #
# parse_api_message – various frame types
# ------------------------------------------------------------------ #


class ParseApiMessageTest(IsolatedAsyncioTestCase):
    """GeminiRealtimeModel.parse_api_message for all supported frame types."""

    # ---- Setup complete ----

    async def test_setup_complete(self) -> None:
        """setupComplete yields ModelSessionCreatedEvent."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps({"setupComplete": {}}),
        )
        assert isinstance(evt, ModelEvents.ModelSessionCreatedEvent)
        self.assertEqual(evt.session_id, "gemini_session")

    # ---- Server content: audio ----

    async def test_audio_delta(self) -> None:
        """modelTurn with inlineData audio yields
        ModelResponseAudioDeltaEvent."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "serverContent": {
                        "modelTurn": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "audio/pcm",
                                        "data": "base64audiodata",
                                    },
                                },
                            ],
                        },
                    },
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelResponseAudioDeltaEvent)
        self.assertEqual(evt.delta, "base64audiodata")
        self.assertEqual(evt.format.rate, 24000)

    async def test_audio_delta_empty_data_returns_none(self) -> None:
        """modelTurn with empty audio data returns None."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "serverContent": {
                        "modelTurn": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "audio/pcm",
                                        "data": "",
                                    },
                                },
                            ],
                        },
                    },
                },
            ),
        )
        self.assertIsNone(evt)

    async def test_audio_response_id_created_client_side(self) -> None:
        """First audio delta generates a client-side response ID."""
        model = _make_model()
        self.assertIsNone(model._response_id)
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "serverContent": {
                        "modelTurn": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "audio/pcm",
                                        "data": "abc123",
                                    },
                                },
                            ],
                        },
                    },
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelResponseAudioDeltaEvent)
        self.assertIsNotNone(model._response_id)
        self.assertTrue(len(model._response_id) > 0)

    # ---- Server content: text ----

    async def test_text_part_yields_transcript_delta(self) -> None:
        """modelTurn with text part yields
        ModelResponseAudioTranscriptDeltaEvent."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "serverContent": {
                        "modelTurn": {
                            "parts": [{"text": "Hello there"}],
                        },
                    },
                },
            ),
        )
        assert isinstance(
            evt,
            ModelEvents.ModelResponseAudioTranscriptDeltaEvent,
        )
        self.assertEqual(evt.delta, "Hello there")

    # ---- Server content: output transcription ----

    async def test_output_transcription(self) -> None:
        """outputTranscription yields
        ModelResponseAudioTranscriptDeltaEvent."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "serverContent": {
                        "outputTranscription": {"text": "spoken text"},
                    },
                },
            ),
        )
        assert isinstance(
            evt,
            ModelEvents.ModelResponseAudioTranscriptDeltaEvent,
        )
        self.assertEqual(evt.delta, "spoken text")

    async def test_output_transcription_empty_returns_none(self) -> None:
        """outputTranscription with empty text returns None."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "serverContent": {
                        "outputTranscription": {"text": ""},
                    },
                },
            ),
        )
        self.assertIsNone(evt)

    # ---- Server content: input transcription ----

    async def test_input_transcription(self) -> None:
        """inputTranscription yields ModelInputTranscriptionDoneEvent."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "serverContent": {
                        "inputTranscription": {"text": "user said this"},
                    },
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelInputTranscriptionDoneEvent)
        self.assertEqual(evt.transcript, "user said this")

    async def test_input_transcription_empty_returns_none(self) -> None:
        """inputTranscription with empty text returns None."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "serverContent": {
                        "inputTranscription": {"text": ""},
                    },
                },
            ),
        )
        self.assertIsNone(evt)

    # ---- Server content: generation / turn complete ----

    async def test_generation_complete(self) -> None:
        """generationComplete yields ModelResponseDoneEvent and clears ID."""
        model = _make_model()
        model._response_id = "resp_gc"
        evt = await model.parse_api_message(
            json.dumps(
                {"serverContent": {"generationComplete": True}},
            ),
        )
        assert isinstance(evt, ModelEvents.ModelResponseDoneEvent)
        self.assertEqual(evt.response_id, "resp_gc")
        self.assertIsNone(model._response_id)

    async def test_turn_complete_with_active_response(self) -> None:
        """turnComplete yields ModelResponseDoneEvent when a response is
        active."""
        model = _make_model()
        model._response_id = "resp_tc"
        evt = await model.parse_api_message(
            json.dumps(
                {"serverContent": {"turnComplete": True}},
            ),
        )
        assert isinstance(evt, ModelEvents.ModelResponseDoneEvent)
        self.assertEqual(evt.response_id, "resp_tc")
        self.assertIsNone(model._response_id)

    async def test_turn_complete_without_active_response_returns_none(
        self,
    ) -> None:
        """turnComplete when no response is active returns None."""
        model = _make_model()
        self.assertIsNone(model._response_id)
        evt = await model.parse_api_message(
            json.dumps(
                {"serverContent": {"turnComplete": True}},
            ),
        )
        self.assertIsNone(evt)

    # ---- Tool call ----

    async def test_tool_call_single_function(self) -> None:
        """toolCall with one functionCall yields a list with one DoneEvent."""
        model = _make_model()
        model._response_id = "resp_1"
        result = await model.parse_api_message(
            json.dumps(
                {
                    "toolCall": {
                        "functionCalls": [
                            {
                                "id": "call_abc",
                                "name": "Bash",
                                "args": {"command": "ls"},
                            },
                        ],
                    },
                },
            ),
        )
        assert isinstance(result, list)
        self.assertEqual(len(result), 1)
        evt = result[0]
        assert isinstance(evt, ModelEvents.ModelResponseToolCallDoneEvent)
        self.assertEqual(evt.tool_call.id, "call_abc")
        self.assertEqual(evt.tool_call.name, "Bash")
        self.assertEqual(
            json.loads(evt.tool_call.input),
            {"command": "ls"},
        )

    async def test_tool_call_multiple_functions(self) -> None:
        """toolCall with two functionCalls yields two DoneEvents."""
        model = _make_model()
        result = await model.parse_api_message(
            json.dumps(
                {
                    "toolCall": {
                        "functionCalls": [
                            {"id": "c1", "name": "Fn1", "args": {}},
                            {"id": "c2", "name": "Fn2", "args": {"x": 1}},
                        ],
                    },
                },
            ),
        )
        assert isinstance(result, list)
        self.assertEqual(len(result), 2)
        names = {e.tool_call.name for e in result}
        self.assertEqual(names, {"Fn1", "Fn2"})

    async def test_tool_call_empty_function_calls_returns_none(self) -> None:
        """toolCall with empty functionCalls list returns None."""
        model = _make_model()
        result = await model.parse_api_message(
            json.dumps({"toolCall": {"functionCalls": []}}),
        )
        self.assertIsNone(result)

    # ---- Tool call cancellation ----

    async def test_tool_call_cancellation(self) -> None:
        """toolCallCancellation yields ModelResponseDoneEvent."""
        model = _make_model()
        model._response_id = "resp_cancel"
        evt = await model.parse_api_message(
            json.dumps(
                {"toolCallCancellation": {"ids": ["call_abc"]}},
            ),
        )
        assert isinstance(evt, ModelEvents.ModelResponseDoneEvent)
        self.assertEqual(evt.response_id, "resp_cancel")
        self.assertIsNone(model._response_id)

    # ---- Session resumption ----

    async def test_session_resumption_update_with_new_handle(self) -> None:
        """sessionResumptionUpdate with newHandle yields
        ModelSessionResumptionEvent."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "sessionResumptionUpdate": {
                        "newHandle": "handle-xyz",
                    },
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelSessionResumptionEvent)
        self.assertEqual(evt.handle, "handle-xyz")
        self.assertEqual(model._session_handle, "handle-xyz")

    async def test_session_resumption_update_without_handle_returns_none(
        self,
    ) -> None:
        """sessionResumptionUpdate without a handle returns None."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps({"sessionResumptionUpdate": {}}),
        )
        self.assertIsNone(evt)

    # ---- Error ----

    async def test_error_event(self) -> None:
        """error frame yields ModelErrorEvent with code and message."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps(
                {
                    "error": {
                        "status": "INVALID_ARGUMENT",
                        "code": 400,
                        "message": "Bad request",
                    },
                },
            ),
        )
        assert isinstance(evt, ModelEvents.ModelErrorEvent)
        self.assertEqual(evt.error_type, "INVALID_ARGUMENT")
        self.assertEqual(evt.code, "400")
        self.assertEqual(evt.message, "Bad request")

    async def test_error_event_missing_fields(self) -> None:
        """error frame with missing fields falls back to 'unknown'."""
        model = _make_model()
        evt = await model.parse_api_message(
            json.dumps({"error": {}}),
        )
        assert isinstance(evt, ModelEvents.ModelErrorEvent)
        self.assertEqual(evt.error_type, "unknown")
        self.assertEqual(evt.code, "unknown")
