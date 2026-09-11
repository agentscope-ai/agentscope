# -*- coding: utf-8 -*-
"""Unit tests for the Gemini Live realtime adapter: cards, the setup
message and frame parsing. Nothing here opens a connection."""
# pylint: disable=protected-access
import base64
import unittest
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.credential import GeminiCredential
from agentscope.message import ToolResultBlock
from agentscope.realtime import (
    GeminiRealtimeModel,
    ModelDisconnectedError,
    TruncationSupport,
)

CRED = GeminiCredential(api_key="key-x")
MODEL = "gemini-3.1-flash-live-preview"
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the weather.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
]


def _dump(events: list) -> list:
    """Events as ``(class name, fields)`` pairs, comparable to literals."""
    return [(type(e).__name__, e.model_dump()) for e in events]


class GeminiCardsTest(unittest.TestCase):
    """The shipped cards and the credential lookup that finds them."""

    def test_cards(self) -> None:
        """Both Live dialog models, tagged with the adapter type."""
        self.assertListEqual(
            [
                (
                    c.name,
                    c.model_type,
                    c.supports_tools,
                    c.max_context_tokens,
                    c.max_session_duration_s,
                    c.output_sample_rate,
                )
                for c in GeminiRealtimeModel.list_models()
            ],
            [
                (
                    "gemini-2.5-flash-native-audio-preview-12-2025",
                    "gemini_realtime",
                    True,
                    131072,
                    900,
                    24000,
                ),
                (
                    "gemini-3.1-flash-live-preview",
                    "gemini_realtime",
                    True,
                    131072,
                    900,
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
                "gemini-2.5-flash-native-audio-preview-12-2025": (
                    "GeminiRealtimeModel"
                ),
                "gemini-3.1-flash-live-preview": "GeminiRealtimeModel",
            },
        )

    def test_unknown_model_name_is_rejected(self) -> None:
        """A name with no card fails at construction."""
        with self.assertRaises(ValueError):
            GeminiRealtimeModel("no-such-model", CRED)

    def test_adapter_facts(self) -> None:
        """Protocol facts of the adapter, constant across its models."""
        model = GeminiRealtimeModel(MODEL, CRED)
        self.assertListEqual(
            [
                model.type,
                model.truncation,
                model.supports_text_input,
                model.input_sample_rate,
                model.output_sample_rate,
            ],
            [
                "gemini_realtime",
                TruncationSupport.SERVER,
                True,
                16000,
                24000,
            ],
        )


class GeminiSetupTest(unittest.TestCase):
    """The setup message sent on connect."""

    def test_automatic_turn_detection(self) -> None:
        """Server VAD, tools and both transcriptions."""
        model = GeminiRealtimeModel(MODEL, CRED)
        self.assertDictEqual(
            model._setup("be nice", TOOLS, ""),
            {
                "setup": {
                    "model": "models/gemini-3.1-flash-live-preview",
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {"voiceName": "Puck"},
                            },
                        },
                    },
                    "systemInstruction": {"parts": [{"text": "be nice"}]},
                    "realtimeInputConfig": {
                        "automaticActivityDetection": {
                            "startOfSpeechSensitivity": (
                                "START_SENSITIVITY_HIGH"
                            ),
                            "endOfSpeechSensitivity": "END_SENSITIVITY_HIGH",
                            "prefixPaddingMs": 300,
                            "silenceDurationMs": 800,
                        },
                    },
                    "outputAudioTranscription": {},
                    "sessionResumption": {},
                    "inputAudioTranscription": {},
                    "tools": [
                        {
                            "functionDeclarations": [
                                {
                                    "name": "get_weather",
                                    "description": "Get the weather.",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {
                                            "city": {"type": "string"},
                                        },
                                        "required": ["city"],
                                    },
                                },
                            ],
                        },
                    ],
                },
            },
        )

    def test_caller_owned_turn_detection(self) -> None:
        """``none`` disables server VAD and its interruptions, and the
        resumption handle is echoed back."""
        model = GeminiRealtimeModel(
            MODEL,
            CRED,
            parameters=GeminiRealtimeModel.Parameters(
                voice="Kore",
                turn_detection="none",
                input_audio_transcription=False,
            ),
        )
        self.assertDictEqual(
            model._setup("hi", None, "h-1"),
            {
                "setup": {
                    "model": "models/gemini-3.1-flash-live-preview",
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "speechConfig": {
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {"voiceName": "Kore"},
                            },
                        },
                    },
                    "systemInstruction": {"parts": [{"text": "hi"}]},
                    "realtimeInputConfig": {
                        "automaticActivityDetection": {"disabled": True},
                        "activityHandling": "NO_INTERRUPTION",
                    },
                    "outputAudioTranscription": {},
                    "sessionResumption": {"handle": "h-1"},
                },
            },
        )


class GeminiParseTest(unittest.TestCase):
    """Server messages -> model events."""

    def setUp(self) -> None:
        """A fresh model per test; no connection is opened."""
        self.model = GeminiRealtimeModel(MODEL, CRED)

    def test_reply_messages(self) -> None:
        """A whole turn: transcription fragments settle when the reply
        starts, audio and transcript stream, usage lands on the end."""
        audio = base64.b64encode(b"\x01\x00").decode()
        messages = [
            {"setupComplete": {}},
            {"serverContent": {"inputTranscription": {"text": "今天"}}},
            {"serverContent": {"inputTranscription": {"text": "天气"}}},
            {
                "serverContent": {
                    "modelTurn": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "audio/pcm;rate=24000",
                                    "data": audio,
                                },
                            },
                        ],
                    },
                },
            },
            {"serverContent": {"outputTranscription": {"text": "晴天"}}},
            {"serverContent": {"generationComplete": True}},
            {
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "responseTokenCount": 5,
                    "totalTokenCount": 15,
                },
                "serverContent": {"turnComplete": True},
            },
        ]
        self.assertListEqual(
            [_dump(self.model._parse(m)) for m in messages],
            [
                [],
                [],
                [],
                [
                    (
                        "InputTranscriptionEvent",
                        {"item_id": AnyString(), "text": "今天天气"},
                    ),
                    ("ResponseCreatedEvent", {"item_id": AnyString()}),
                    (
                        "AudioDeltaEvent",
                        {
                            "item_id": AnyString(),
                            "pcm": b"\x01\x00",
                            "sample_rate": 24000,
                        },
                    ),
                ],
                [
                    (
                        "TranscriptDeltaEvent",
                        {"item_id": AnyString(), "delta": "晴天"},
                    ),
                ],
                [],
                [
                    (
                        "ResponseDoneEvent",
                        {
                            "item_id": AnyString(),
                            "input_tokens": 10,
                            "output_tokens": 5,
                        },
                    ),
                ],
            ],
        )

    def test_tool_call_ends_the_turn(self) -> None:
        """A tool call hands the floor back, so the reply is closed with
        it and the caller can run the tool."""
        self.assertListEqual(
            _dump(
                self.model._parse(
                    {
                        "toolCall": {
                            "functionCalls": [
                                {
                                    "id": "c1",
                                    "name": "get_weather",
                                    "args": {"city": "上海"},
                                },
                            ],
                        },
                    },
                ),
            ),
            [
                ("ResponseCreatedEvent", {"item_id": AnyString()}),
                (
                    "ToolCallEvent",
                    {
                        "item_id": AnyString(),
                        "tool_call": {
                            "type": "tool_call",
                            "id": "c1",
                            "name": "get_weather",
                            "input": '{"city": "上海"}',
                            "state": "pending",
                            "suggested_rules": [],
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    },
                ),
                (
                    "ResponseDoneEvent",
                    {
                        "item_id": AnyString(),
                        "input_tokens": 0,
                        "output_tokens": 0,
                    },
                ),
            ],
        )

    def test_interrupted_is_the_barge_in_notice(self) -> None:
        """``interrupted`` is the only signal that the user spoke over
        the reply; the turn is dropped without a done event."""
        self.model._parse(
            {"serverContent": {"outputTranscription": {"text": "晴"}}},
        )
        self.assertListEqual(
            [
                _dump(
                    self.model._parse(
                        {"serverContent": {"interrupted": True}},
                    ),
                ),
                _dump(
                    self.model._parse(
                        {"serverContent": {"turnComplete": True}},
                    ),
                ),
            ],
            [
                [
                    (
                        "SpeechStartedEvent",
                        {"item_id": AnyString(), "at_ms": 0},
                    ),
                ],
                [],
            ],
        )

    def test_setup_complete_releases_connect(self) -> None:
        """``connect()`` waits on the setup acknowledgement, which the
        reader flags when it arrives."""
        self.assertListEqual(
            [
                self.model._ready.is_set(),
                self.model._parse({"setupComplete": {}}),
                self.model._ready.is_set(),
            ],
            [False, [], True],
        )

    def test_session_messages(self) -> None:
        """Resumption handles are kept; ``goAway`` and tool-call
        cancellation carry no event."""
        handles = [
            self.model._parse(
                {
                    "sessionResumptionUpdate": {
                        "newHandle": "h-1",
                        "resumable": True,
                    },
                },
            ),
            self.model.resumption_handle,
            _dump(
                self.model._parse({"toolCallCancellation": {"ids": ["c1"]}}),
            ),
            _dump(self.model._parse({"goAway": {"timeLeft": "10s"}})),
        ]
        self.assertListEqual(
            handles,
            [
                [],
                "h-1",
                [],
                [],
            ],
        )


class GeminiSendTest(IsolatedAsyncioTestCase):
    """The frames each input method puts on the wire."""

    def setUp(self) -> None:
        """Capture what would be sent instead of sending it."""
        self.model = GeminiRealtimeModel(MODEL, CRED)
        self.sent: list[dict] = []

        async def capture(payload: dict) -> None:
            """Record one frame."""
            self.sent.append(payload)

        self.model._send = capture  # type: ignore[method-assign]

    async def test_text_turn_and_tool_result(self) -> None:
        """A text turn is complete on arrival; a tool result resumes the
        model without asking for a response."""
        await self.model.push_text("你好")
        await self.model.push_tool_result(
            ToolResultBlock(id="c1", name="get_weather", output="晴天"),
        )
        await self.model.request_response()
        self.assertListEqual(
            self.sent,
            [
                {
                    "clientContent": {
                        "turns": [
                            {"role": "user", "parts": [{"text": "你好"}]},
                        ],
                        "turnComplete": True,
                    },
                },
                {
                    "toolResponse": {
                        "functionResponses": [
                            {
                                "id": "c1",
                                "name": "get_weather",
                                "response": {"output": "晴天"},
                            },
                        ],
                    },
                },
            ],
        )

    async def test_caller_owned_turn_brackets_the_audio(self) -> None:
        """With server VAD off, the first chunk opens the activity window
        and the commit closes it."""
        self.model.parameters = GeminiRealtimeModel.Parameters(
            turn_detection="none",
        )
        await self.model.push_audio(b"\x01\x00")
        await self.model.push_audio(b"\x02\x00")
        await self.model.commit_turn()
        await self.model.commit_turn()
        self.assertListEqual(
            self.sent,
            [
                {"realtimeInput": {"activityStart": {}}},
                {
                    "realtimeInput": {
                        "audio": {
                            "mimeType": "audio/pcm;rate=16000",
                            "data": "AQA=",
                        },
                    },
                },
                {
                    "realtimeInput": {
                        "audio": {
                            "mimeType": "audio/pcm;rate=16000",
                            "data": "AgA=",
                        },
                    },
                },
                {"realtimeInput": {"activityEnd": {}}},
            ],
        )

    async def test_server_turn_detection_sends_audio_only(self) -> None:
        """With server VAD the caller never brackets anything."""
        await self.model.push_audio(b"\x01\x00")
        await self.model.commit_turn()
        self.assertListEqual(
            self.sent,
            [
                {
                    "realtimeInput": {
                        "audio": {
                            "mimeType": "audio/pcm;rate=16000",
                            "data": "AQA=",
                        },
                    },
                },
            ],
        )


class GeminiDisconnectTest(IsolatedAsyncioTestCase):
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
                close = Close(1011, "connection lifetime")
                raise ConnectionClosedError(close, close, True)

        model = GeminiRealtimeModel(MODEL, CRED)
        model._ws = ClosedSocket()

        with self.assertRaises(ModelDisconnectedError) as ctx:
            await model.push_audio(b"\x00\x00")
        self.assertEqual(
            (str(ctx.exception), model._ws),
            ("1011 (internal error) connection lifetime", None),
        )

    async def test_send_before_connect(self) -> None:
        """No socket at all is the same condition."""
        model = GeminiRealtimeModel(MODEL, CRED)
        with self.assertRaises(ModelDisconnectedError):
            await model.push_text("你好")
