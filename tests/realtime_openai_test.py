# -*- coding: utf-8 -*-
"""Unit tests for the OpenAI realtime adapter: cards, session config and
frame parsing. Nothing here opens a connection."""
# pylint: disable=protected-access
import base64
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from utils import AnyString

from agentscope.credential import OpenAICredential
from agentscope.realtime import (
    ModelDisconnectedError,
    OpenAIRealtimeModel,
    TruncationSupport,
)
from agentscope.realtime import _events as me
from agentscope.message import ToolResultBlock

CRED = OpenAICredential(api_key="sk-x")
TRANSCRIPTION_DONE = "conversation.item.input_audio_transcription.completed"


class OpenAICardsTest(unittest.TestCase):
    """The shipped cards and the credential lookup that finds them."""

    def test_cards(self) -> None:
        """Every card is tagged with the adapter type and its limits."""
        self.assertListEqual(
            [
                (
                    c.name,
                    c.model_type,
                    c.supports_tools,
                    c.max_context_tokens,
                    c.input_sample_rate,
                    c.output_sample_rate,
                )
                for c in OpenAIRealtimeModel.list_models()
            ],
            [
                (
                    "gpt-realtime-1.5",
                    "openai_realtime",
                    True,
                    32000,
                    24000,
                    24000,
                ),
                (
                    "gpt-realtime-2.1-mini",
                    "openai_realtime",
                    True,
                    128000,
                    24000,
                    24000,
                ),
                (
                    "gpt-realtime-2.1",
                    "openai_realtime",
                    True,
                    128000,
                    24000,
                    24000,
                ),
                (
                    "gpt-realtime-2",
                    "openai_realtime",
                    True,
                    128000,
                    24000,
                    24000,
                ),
            ],
        )

    def test_credential_maps_card_back_to_class(self) -> None:
        """The service-layer lookup: card.model_type -> class, no scan."""
        classes = {c.type: c for c in CRED.get_realtime_model_classes()}
        self.assertDictEqual(
            {
                card.name: classes[card.model_type].__name__
                for card in CRED.list_realtime_models()
            },
            {
                "gpt-realtime-1.5": "OpenAIRealtimeModel",
                "gpt-realtime-2": "OpenAIRealtimeModel",
                "gpt-realtime-2.1": "OpenAIRealtimeModel",
                "gpt-realtime-2.1-mini": "OpenAIRealtimeModel",
            },
        )

    def test_unknown_model_name_is_rejected(self) -> None:
        """A name with no card fails at construction."""
        with self.assertRaises(ValueError):
            OpenAIRealtimeModel("no-such-model", CRED)

    def test_adapter_facts(self) -> None:
        """Protocol facts are constant across the OpenAI models."""
        model = OpenAIRealtimeModel("gpt-realtime-2.1", CRED)
        self.assertListEqual(
            [
                model.type,
                model.truncation,
                model.supports_text_input,
                model.input_sample_rate,
                model.output_sample_rate,
            ],
            [
                "openai_realtime",
                TruncationSupport.EXPLICIT,
                True,
                24000,
                24000,
            ],
        )


class OpenAISessionUpdateTest(unittest.TestCase):
    """The GA session.update payload sent on connect."""

    def test_server_vad_payload(self) -> None:
        """Server VAD, transcription and tools, in the GA session shape;
        the toolkit's chat-style tool wrapper is flattened."""
        model = OpenAIRealtimeModel("gpt-realtime-2.1", CRED)
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Weather by city.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]
        self.assertDictEqual(
            model._session_update("be nice", tools),
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "instructions": "be nice",
                    "output_modalities": ["audio"],
                    "audio": {
                        "input": {
                            "format": {
                                "type": "audio/pcm",
                                "rate": 24000,
                            },
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.5,
                                "prefix_padding_ms": 300,
                                "silence_duration_ms": 500,
                            },
                            "transcription": {
                                "model": "gpt-4o-mini-transcribe",
                            },
                        },
                        "output": {
                            "format": {
                                "type": "audio/pcm",
                                "rate": 24000,
                            },
                            "voice": "marin",
                        },
                    },
                    "tools": [
                        {
                            "type": "function",
                            "name": "get_weather",
                            "description": "Weather by city.",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                            },
                        },
                    ],
                },
            },
        )

    def test_semantic_vad_payload_without_transcription(self) -> None:
        """Semantic VAD takes an eagerness, not a threshold; an empty
        transcription model drops the block entirely."""
        model = OpenAIRealtimeModel(
            "gpt-realtime-1.5",
            CRED,
            parameters=OpenAIRealtimeModel.Parameters(
                voice="cedar",
                turn_detection="semantic_vad",
                vad_eagerness="low",
                input_audio_transcription="",
            ),
        )
        self.assertDictEqual(
            model._session_update("be nice", None),
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "instructions": "be nice",
                    "output_modalities": ["audio"],
                    "audio": {
                        "input": {
                            "format": {
                                "type": "audio/pcm",
                                "rate": 24000,
                            },
                            "turn_detection": {
                                "type": "semantic_vad",
                                "eagerness": "low",
                            },
                        },
                        "output": {
                            "format": {
                                "type": "audio/pcm",
                                "rate": 24000,
                            },
                            "voice": "cedar",
                        },
                    },
                },
            },
        )

    def test_turn_detection_none_hands_endpointing_to_caller(self) -> None:
        """``none`` sends null so the caller commits its own turns."""
        model = OpenAIRealtimeModel(
            "gpt-realtime-2.1",
            CRED,
            parameters=OpenAIRealtimeModel.Parameters(turn_detection="none"),
        )
        self.assertDictEqual(
            model._session_update("x", None),
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "instructions": "x",
                    "output_modalities": ["audio"],
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "turn_detection": None,
                            "transcription": {
                                "model": "gpt-4o-mini-transcribe",
                            },
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "voice": "marin",
                        },
                    },
                },
            },
        )

    def test_failed_response_is_an_error(self) -> None:
        """A ``response.done`` with status ``failed`` is not a reply."""
        model = OpenAIRealtimeModel("gpt-realtime-2.1", CRED)
        model._parse({"type": "response.created", "response": {"id": "r"}})
        self.assertEqual(
            model._parse(
                {
                    "type": "response.done",
                    "response": {
                        "id": "r",
                        "status": "failed",
                        "status_details": {
                            "type": "failed",
                            "error": {
                                "code": "server_error",
                                "message": "boom",
                            },
                        },
                    },
                },
            ),
            me.ModelErrorEvent(code="server_error", message="boom"),
        )


class OpenAIParseTest(unittest.TestCase):
    """Server frames -> model events."""

    def setUp(self) -> None:
        """Open a response so deltas have an item to attach to."""
        self.model = OpenAIRealtimeModel("gpt-realtime-2.1", CRED)

    def test_response_frames(self) -> None:
        """A whole turn: created, first item, deltas, usage, done."""
        frames = [
            {"type": "response.created", "response": {"id": "resp_1"}},
            {
                "type": "response.output_item.added",
                "response_id": "resp_1",
                "item": {"id": "item_1", "type": "message"},
            },
            {
                "type": "response.output_audio_transcript.delta",
                "item_id": "item_1",
                "delta": "hello",
            },
            {
                "type": "response.output_audio.delta",
                "item_id": "item_1",
                "delta": base64.b64encode(b"\x01\x00").decode(),
            },
            {
                "type": "response.done",
                "response": {
                    "id": "resp_1",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
            {"type": "session.updated"},
        ]
        self.assertListEqual(
            [self.model._parse(f) for f in frames],
            [
                None,
                me.ResponseCreatedEvent(item_id="item_1"),
                me.TranscriptDeltaEvent(item_id="item_1", delta="hello"),
                me.AudioDeltaEvent(
                    item_id="item_1",
                    pcm=b"\x01\x00",
                    sample_rate=24000,
                ),
                me.ResponseDoneEvent(
                    item_id="item_1",
                    input_tokens=10,
                    output_tokens=5,
                ),
                None,
            ],
        )

    def test_pre_ga_audio_names_are_accepted(self) -> None:
        """OpenAI-compatible deployments may still send the beta names."""
        self.model._item_id = "item_1"
        self.assertListEqual(
            [
                self.model._parse(
                    {"type": "response.audio_transcript.delta", "delta": "hi"},
                ),
                self.model._parse(
                    {
                        "type": "response.audio.delta",
                        "delta": base64.b64encode(b"\x02\x00").decode(),
                    },
                ),
            ],
            [
                me.TranscriptDeltaEvent(item_id="item_1", delta="hi"),
                me.AudioDeltaEvent(
                    item_id="item_1",
                    pcm=b"\x02\x00",
                    sample_rate=24000,
                ),
            ],
        )

    def test_user_turn_frames(self) -> None:
        """The provider's VAD and the settled input transcript."""
        frames = [
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "user_1",
                "audio_start_ms": 120,
            },
            {
                "type": "input_audio_buffer.speech_stopped",
                "item_id": "user_1",
                "audio_end_ms": 980,
            },
            {
                "type": TRANSCRIPTION_DONE,
                "item_id": "user_1",
                "transcript": "what is the weather",
            },
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "code": "invalid_value",
                    "message": "boom",
                },
            },
        ]
        self.assertListEqual(
            [self.model._parse(f) for f in frames],
            [
                me.SpeechStartedEvent(item_id="user_1", at_ms=120),
                me.SpeechEndedEvent(item_id="user_1", at_ms=980),
                me.InputTranscriptionEvent(
                    item_id="user_1",
                    text="what is the weather",
                ),
                me.ModelErrorEvent(code="invalid_value", message="boom"),
            ],
        )

    def test_tool_call_frame(self) -> None:
        """The done frame carries the whole call, and it belongs to the
        response's first item, not the function call item."""
        self.model._item_id = "item_1"
        event = self.model._parse(
            {
                "type": "response.function_call_arguments.done",
                "item_id": "item_2",
                "call_id": "call_1",
                "name": "get_weather",
                "arguments": '{"city":"sh"}',
            },
        )
        self.assertDictEqual(
            event.model_dump(),
            {
                "item_id": "item_1",
                "tool_call": {
                    "type": "tool_call",
                    "id": "call_1",
                    "name": "get_weather",
                    "input": '{"city":"sh"}',
                    "state": "pending",
                    "suggested_rules": [],
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            },
        )


class OpenAIWireTest(IsolatedAsyncioTestCase):
    """The client frames the adapter writes."""

    def setUp(self) -> None:
        """Capture what would go on the wire."""
        self.model = OpenAIRealtimeModel("gpt-realtime-2.1", CRED)
        self.sent: list[dict] = []

        async def capture(payload: dict) -> None:
            """Record one frame instead of sending it."""
            self.sent.append(payload)

        self.model._send = capture  # type: ignore[method-assign]

    async def test_audio_and_commit(self) -> None:
        """Audio is appended base64-encoded, then committed."""
        await self.model.push_audio(b"\x01\x00")
        await self.model.commit_turn()
        self.assertListEqual(
            self.sent,
            [
                {"type": "input_audio_buffer.append", "audio": "AQA="},
                {"type": "input_audio_buffer.commit"},
            ],
        )

    async def test_text_turn(self) -> None:
        """A text turn is one item plus a response request."""
        await self.model.push_text("hello")
        self.assertListEqual(
            self.sent,
            [
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "hello"}],
                    },
                },
                {"type": "response.create"},
            ],
        )

    async def test_tool_result(self) -> None:
        """A tool result is a ``function_call_output`` item."""
        await self.model.push_tool_result(
            ToolResultBlock(
                type="tool_result",
                id="call_1",
                name="get_weather",
                output="sunny",
            ),
        )
        self.assertListEqual(
            self.sent,
            [
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": "call_1",
                        "output": "sunny",
                    },
                },
            ],
        )

    async def test_barge_in_truncates_then_cancels(self) -> None:
        """A barge-in rewrites the item to the audio heard and cancels
        the response; with none in flight the cancel is skipped."""
        await self.model.truncate("item_1", 1200, "hel")
        await self.model.cancel_response()
        self.model._response_id = "resp_1"
        await self.model.cancel_response()
        self.assertListEqual(
            self.sent,
            [
                {
                    "type": "conversation.item.truncate",
                    "item_id": "item_1",
                    "content_index": 0,
                    "audio_end_ms": 1200,
                },
                {"type": "response.cancel"},
            ],
        )


class OpenAIDisconnectTest(IsolatedAsyncioTestCase):
    """A closed WebSocket surfaces as ModelDisconnectedError."""

    async def test_send_on_closed_socket(self) -> None:
        """websockets' ConnectionClosed becomes the realtime-level error
        and the socket reference is dropped."""
        from websockets.exceptions import ConnectionClosedError
        from websockets.frames import Close

        class ClosedSocket:
            """Raises like a socket the provider already closed."""

            async def send(self, _payload: str) -> None:
                """Fail with the provider's close frame."""
                close = Close(1000, "session expired")
                raise ConnectionClosedError(close, close, True)

        model = OpenAIRealtimeModel("gpt-realtime-2.1", CRED)
        model._ws = ClosedSocket()

        with self.assertRaises(ModelDisconnectedError) as ctx:
            await model.push_audio(b"\x00\x00")
        self.assertEqual(
            (str(ctx.exception), model._ws),
            ("1000 (OK) session expired", None),
        )

    async def test_send_before_connect(self) -> None:
        """No socket at all is the same condition."""
        model = OpenAIRealtimeModel("gpt-realtime-2.1", CRED)
        with self.assertRaises(ModelDisconnectedError):
            await model.commit_turn()
