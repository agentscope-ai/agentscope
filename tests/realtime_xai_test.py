# -*- coding: utf-8 -*-
"""Unit tests for the xAI Grok voice realtime adapter: cards, session
config and frame parsing. Nothing here opens a connection."""
# pylint: disable=protected-access
import base64
import unittest
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.credential import XAICredential
from agentscope.message import TextBlock, ToolResultBlock
from agentscope.realtime import (
    ModelDisconnectedError,
    TruncationSupport,
    XAIRealtimeModel,
)
from agentscope.realtime import _events as me

CRED = XAICredential(api_key="sk-x")
TRANSCRIPTION_DONE = "conversation.item.input_audio_transcription.completed"


class XAICardsTest(unittest.TestCase):
    """The adapter lists its own cards, tagged with its type."""

    def test_cards(self) -> None:
        """Only the two active Grok voice models ship a card."""
        self.assertListEqual(
            [
                (
                    c.name,
                    c.model_type,
                    c.status,
                    c.supports_tools,
                    c.input_sample_rate,
                    c.output_sample_rate,
                )
                for c in XAIRealtimeModel.list_models()
            ],
            [
                (
                    "grok-voice-latest",
                    "xai_realtime",
                    "active",
                    True,
                    24000,
                    24000,
                ),
                (
                    "grok-voice-think-fast-2.0",
                    "xai_realtime",
                    "active",
                    True,
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
                "grok-voice-latest": "XAIRealtimeModel",
                "grok-voice-think-fast-2.0": "XAIRealtimeModel",
            },
        )

    def test_unknown_model_name_is_rejected(self) -> None:
        """A name with no card fails at construction."""
        with self.assertRaises(ValueError):
            XAIRealtimeModel("no-such-model", CRED)

    def test_adapter_facts(self) -> None:
        """Grok takes text turns but documents no truncate frame."""
        model = XAIRealtimeModel("grok-voice-latest", CRED)
        self.assertListEqual(
            [model.type, model.truncation, model.supports_text_input],
            ["xai_realtime", TruncationSupport.NONE, True],
        )


class XAISessionUpdateTest(unittest.TestCase):
    """The session.update payload sent on connect."""

    def test_server_vad_payload(self) -> None:
        """Defaults, tools and a language hint, VAD left to the server."""
        model = XAIRealtimeModel(
            "grok-voice-latest",
            CRED,
            parameters=XAIRealtimeModel.Parameters(language_hint="es-MX"),
        )
        self.assertDictEqual(
            model._session_update("be nice", [{"type": "function"}]),
            {
                "type": "session.update",
                "session": {
                    "instructions": "be nice",
                    "voice": "eve",
                    "reasoning": {"effort": "high"},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.85,
                        "silence_duration_ms": 500,
                    },
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "transcription": {"language_hint": "es-MX"},
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                        },
                    },
                    "tools": [{"type": "function"}],
                },
            },
        )

    def test_turn_detection_none_hands_endpointing_to_caller(self) -> None:
        """``none`` sends a null detection type and no tools without any."""
        model = XAIRealtimeModel(
            "grok-voice-think-fast-2.0",
            CRED,
            parameters=XAIRealtimeModel.Parameters(
                turn_detection="none",
                voice="ara",
                reasoning_effort="none",
            ),
        )
        self.assertDictEqual(
            model._session_update("be brief", None),
            {
                "type": "session.update",
                "session": {
                    "instructions": "be brief",
                    "voice": "ara",
                    "reasoning": {"effort": "none"},
                    "turn_detection": {"type": None},
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                        },
                    },
                },
            },
        )


class XAIParseTest(unittest.TestCase):
    """Server frames -> model events."""

    def setUp(self) -> None:
        """Open a response so deltas have an item to attach to."""
        self.model = XAIRealtimeModel("grok-voice-latest", CRED)
        self.model._parse(
            {"type": "response.created", "response": {"id": "resp_001"}},
        )

    def test_frames(self) -> None:
        """Every frame the adapter knows maps to one model event."""
        frames = [
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "msg_003",
                "audio_start_ms": 120,
            },
            {
                "type": "input_audio_buffer.speech_stopped",
                "item_id": "msg_003",
                "audio_end_ms": 980,
            },
            {
                "type": TRANSCRIPTION_DONE,
                "item_id": "msg_003",
                "transcript": "Hello, how are you?",
            },
            {
                "type": "response.output_audio_transcript.delta",
                "item_id": "msg_008",
                "delta": "Hello! I'm doing",
            },
            {
                "type": "response.output_audio.delta",
                "item_id": "msg_008",
                "delta": base64.b64encode(b"\x01\x00").decode(),
            },
            {
                "type": "response.done",
                "response": {
                    "id": "resp_001",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
            {
                "type": "error",
                "error": {"code": "invalid_audio_format", "message": "boom"},
            },
            {"type": "session.updated"},
        ]
        self.assertListEqual(
            [self.model._parse(f) for f in frames],
            [
                me.SpeechStartedEvent(item_id="msg_003", at_ms=120),
                me.SpeechEndedEvent(item_id="msg_003", at_ms=980),
                me.InputTranscriptionEvent(
                    item_id="msg_003",
                    text="Hello, how are you?",
                ),
                me.TranscriptDeltaEvent(
                    item_id="resp_001",
                    delta="Hello! I'm doing",
                ),
                me.AudioDeltaEvent(
                    item_id="resp_001",
                    pcm=b"\x01\x00",
                    sample_rate=24000,
                ),
                me.ResponseDoneEvent(
                    item_id="resp_001",
                    input_tokens=10,
                    output_tokens=5,
                ),
                me.ModelErrorEvent(
                    code="invalid_audio_format",
                    message="boom",
                ),
                None,
            ],
        )

    def test_response_created_frame(self) -> None:
        """The response id groups every event of the turn."""
        self.assertEqual(
            self.model._parse(
                {"type": "response.created", "response": {"id": "resp_002"}},
            ),
            me.ResponseCreatedEvent(item_id="resp_002"),
        )

    def test_tool_call_done_frame_is_authoritative(self) -> None:
        """Accumulated deltas are only a fallback for a done frame that
        omits ``arguments``."""
        self.model._parse(
            {
                "type": "response.function_call_arguments.delta",
                "call_id": "call_001",
                "delta": '{"location":',
            },
        )
        with_args = self.model._parse(
            {
                "type": "response.function_call_arguments.done",
                "item_id": "msg_009",
                "call_id": "call_001",
                "name": "get_weather",
                "arguments": '{"location": "San Francisco"}',
            },
        )
        self.model._parse(
            {
                "type": "response.function_call_arguments.delta",
                "call_id": "call_002",
                "delta": '{"a": 1}',
            },
        )
        without_args = self.model._parse(
            {
                "type": "response.function_call_arguments.done",
                "call_id": "call_002",
                "name": "f",
            },
        )
        self.assertListEqual(
            [with_args.model_dump(), without_args.model_dump()],
            [
                {
                    "item_id": "resp_001",
                    "tool_call": {
                        "type": "tool_call",
                        "id": "call_001",
                        "name": "get_weather",
                        "input": '{"location": "San Francisco"}',
                        "state": "pending",
                        "suggested_rules": [],
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                },
                {
                    "item_id": "resp_001",
                    "tool_call": {
                        "type": "tool_call",
                        "id": "call_002",
                        "name": "f",
                        "input": '{"a": 1}',
                        "state": "pending",
                        "suggested_rules": [],
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                },
            ],
        )


class XAIClientFramesTest(IsolatedAsyncioTestCase):
    """The frames the adapter puts on the wire."""

    def setUp(self) -> None:
        """Capture sends instead of opening a socket."""
        self.model = XAIRealtimeModel("grok-voice-latest", CRED)
        self.sent: list[dict] = []

        async def capture(payload: dict) -> None:
            """Record one frame."""
            self.sent.append(payload)

        self.model._send = capture  # type: ignore[method-assign]

    async def test_text_turn(self) -> None:
        """A text turn is one item plus a response request."""
        await self.model.push_text("hi there")
        self.assertListEqual(
            self.sent,
            [
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "hi there"},
                        ],
                    },
                },
                {"type": "response.create"},
            ],
        )

    async def test_tool_result_and_commit(self) -> None:
        """A tool result is a ``function_call_output`` item."""
        await self.model.push_tool_result(
            ToolResultBlock(
                id="call_001",
                name="get_weather",
                output=[TextBlock(text="sunny")],
            ),
        )
        await self.model.commit_turn()
        self.assertListEqual(
            self.sent,
            [
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": "call_001",
                        "output": "sunny",
                    },
                },
                {"type": "input_audio_buffer.commit"},
            ],
        )

    async def test_barge_in_cancels_without_truncating(self) -> None:
        """A barge-in cancels the response; there is no truncate frame."""
        self.model._parse(
            {"type": "response.created", "response": {"id": "resp_001"}},
        )
        await self.model.cancel_response()
        await self.model.truncate("resp_001", 1500, "Hello! I'm")
        self.assertListEqual(self.sent, [{"type": "response.cancel"}])


class XAIDisconnectTest(IsolatedAsyncioTestCase):
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
                close = Close(1007, "idle 180s")
                raise ConnectionClosedError(close, close, True)

        model = XAIRealtimeModel("grok-voice-latest", CRED)
        model._ws = ClosedSocket()

        with self.assertRaises(ModelDisconnectedError) as ctx:
            await model.push_audio(b"\x00\x00")
        self.assertEqual(
            (str(ctx.exception), model._ws),
            ("1007 (invalid frame payload data) idle 180s", None),
        )

    async def test_send_before_connect(self) -> None:
        """No socket at all is the same condition."""
        model = XAIRealtimeModel("grok-voice-latest", CRED)
        with self.assertRaises(ModelDisconnectedError):
            await model.commit_turn()
