# -*- coding: utf-8 -*-
"""Unit tests for the Gemini Live realtime adapter: cards, the setup
message and frame parsing. Nothing here opens a connection."""
# pylint: disable=protected-access
import asyncio
import base64
import unittest
from itertools import product
from types import SimpleNamespace
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from utils import AnyString

from agentscope.credential import GeminiCredential
from agentscope.message import ToolResultBlock, ToolResultState
from agentscope.realtime import (
    GeminiRealtimeModel,
    ModelDisconnectedError,
)

CRED = GeminiCredential(api_key="key-x")
MODEL = "gemini-3.1-flash-live-preview"
THINKING_MODEL = "gemini-3.8-live-extended-thinking"
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

    def test_credential_maps_card_back_to_class(self) -> None:
        """The service-layer lookup: card.model_type -> class, no scan."""
        cards = GeminiRealtimeModel.list_models()
        self.assertTrue(cards)
        classes = {c.type: c for c in CRED.get_realtime_model_classes()}
        self.assertDictEqual(
            {
                card.name: classes[card.model_type]
                for card in CRED.list_realtime_models()
            },
            {card.name: GeminiRealtimeModel for card in cards},
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
                model.supports_text_input,
                model.input_sample_rate,
                model.output_sample_rate,
            ],
            [
                "gemini_realtime",
                True,
                16000,
                24000,
            ],
        )


class GeminiSetupTest(unittest.TestCase):
    """The setup message sent on connect."""

    def test_gemini_38_setup(self) -> None:
        """The new Live model reuses the supported audio session setup."""
        expected = GeminiRealtimeModel(MODEL, CRED)._setup(
            "be nice",
            TOOLS,
            "",
        )
        expected["setup"]["model"] = "models/gemini-3.8-live"
        model = GeminiRealtimeModel("gemini-3.8-live", CRED)
        self.assertDictEqual(model._setup("be nice", TOOLS, ""), expected)

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


class GeminiThinkingTest(unittest.TestCase):
    """Extended Thinking configuration and interaction boundaries."""

    def test_setup_and_model_discovery(self) -> None:
        """Only the new model exposes reasoning and non-blocking tools."""
        cards = CRED.list_realtime_models()
        self.assertEqual(
            {
                card.name: "thinking_level"
                in card.parameter_schema["properties"]
                for card in cards
            },
            {
                "gemini-2.5-flash-native-audio-preview-12-2025": False,
                MODEL: False,
                "gemini-3.8-live": False,
                THINKING_MODEL: True,
            },
        )
        for level in (None, "low", "medium", "high"):
            with self.subTest(level=level):
                expected = GeminiRealtimeModel(MODEL, CRED)._setup(
                    "be nice",
                    TOOLS,
                    "handle",
                )
                setup = expected["setup"]
                setup["model"] = f"models/{THINKING_MODEL}"
                setup["tools"][0]["functionDeclarations"][0][
                    "behavior"
                ] = "NON_BLOCKING"
                if level:
                    setup["generationConfig"]["thinkingConfig"] = {
                        "thinkingLevel": level.upper(),
                    }
                model = GeminiRealtimeModel(
                    THINKING_MODEL,
                    CRED,
                    parameters=GeminiRealtimeModel.Parameters(
                        thinking_level=level,
                    ),
                )
                self.assertEqual(
                    model._setup("be nice", TOOLS, "handle"),
                    expected,
                )

    def test_thinking_capability_comes_from_card(self) -> None:
        """Aliases and hidden UI parameters do not change protocol mode."""
        original = GeminiRealtimeModel(THINKING_MODEL, CRED).card
        self.assertEqual(
            {
                "enabled": original.supports_extended_thinking,
                "release": original.model_dump(mode="json")["release_date"],
                "session_limit": original.max_session_duration_s,
            },
            {"enabled": True, "release": "2026-09-15", "session_limit": 900},
        )
        for name in (THINKING_MODEL, "custom-thinking-alias"):
            for enabled in (False, True):
                with self.subTest(name=name, enabled=enabled):
                    card = original.model_copy(
                        update={
                            "name": name,
                            "supports_extended_thinking": enabled,
                            "parameter_schema": {},
                        },
                    )
                    model = GeminiRealtimeModel(name, CRED, model_card=card)
                    declaration = model._setup("", TOOLS, "")["setup"][
                        "tools"
                    ][0]["functionDeclarations"][0]
                    self.assertEqual(
                        {
                            "thinking": model._extended_thinking,
                            "behavior": declaration.get("behavior"),
                            "events": [
                                type(event).__name__
                                for event in model._parse(
                                    {
                                        "serverContent": {
                                            "interactionStatus": "IN_PROGRESS",
                                        },
                                    },
                                )
                            ],
                        },
                        {
                            "thinking": enabled,
                            "behavior": "NON_BLOCKING" if enabled else None,
                            "events": ["ResponseCreatedEvent"]
                            if enabled
                            else [],
                        },
                    )

    def test_setup_acknowledgment_survives_mixed_frames(self) -> None:
        """A setup acknowledgment is processed before content early returns."""
        extras: list[dict] = [
            {},
            {"interactionStatus": "IN_PROGRESS"},
            {"serverContent": {"interactionStatus": "IN_PROGRESS"}},
        ]
        for extra in extras:
            with self.subTest(extra=extra):
                model = GeminiRealtimeModel(THINKING_MODEL, CRED)
                events = model._parse({"setupComplete": {}, **extra})
                self.assertEqual(
                    {"ready": model._ready.is_set(), "events": _dump(events)},
                    {
                        "ready": True,
                        "events": [
                            (
                                "ResponseCreatedEvent",
                                {
                                    "item_id": model._response_id,
                                },
                            ),
                        ]
                        if extra
                        else [],
                    },
                )

    def test_invalid_thinking_configuration(self) -> None:
        """Unsupported levels and older models fail before connecting."""
        with self.assertRaises(ValueError):
            GeminiRealtimeModel.Parameters(thinking_level="minimal")
        for card in GeminiRealtimeModel.list_models():
            if card.name == THINKING_MODEL:
                continue
            with self.subTest(model=card.name):
                with self.assertRaisesRegex(ValueError, "requires"):
                    GeminiRealtimeModel(
                        card.name,
                        CRED,
                        parameters=GeminiRealtimeModel.Parameters(
                            thinking_level="low",
                        ),
                    )

    def test_background_interaction(self) -> None:
        """Fillers and tools share one response until final IDLE."""
        model = GeminiRealtimeModel(THINKING_MODEL, CRED)
        messages = [
            {"serverContent": {"interactionStatus": "IN_PROGRESS"}},
            {
                "serverContent": {
                    "modelTurn": {
                        "parts": [{"thought": True, "text": "Plan"}],
                    },
                },
            },
            {
                "serverContent": {
                    "modelTurn": {
                        "parts": [{"inlineData": {"data": "AQA="}}],
                    },
                    "outputTranscription": {"text": "Checking."},
                    "turnComplete": True,
                    "interactionStatus": "IN_PROGRESS",
                },
            },
            {
                "toolCall": {
                    "functionCalls": [
                        {"id": "c1", "name": "get_weather", "args": {}},
                    ],
                },
                "interactionStatus": "IN_PROGRESS",
            },
            {"serverContent": {"turnComplete": True}},
            {
                "serverContent": {
                    "outputTranscription": {"text": "Sunny."},
                    "interactionStatus": "IDLE",
                    "turnComplete": True,
                },
                "usageMetadata": {
                    "promptTokenCount": 12,
                    "responseTokenCount": 7,
                },
            },
            {"serverContent": {"interactionStatus": "IDLE"}},
        ]
        with patch(
            "agentscope.realtime._gemini._model.uuid.uuid4",
            return_value=SimpleNamespace(hex="r1"),
        ):
            actual = [_dump(model._parse(frame)) for frame in messages]
        self.assertEqual(
            actual,
            [
                [("ResponseCreatedEvent", {"item_id": "r1"})],
                [],
                [
                    (
                        "AudioDeltaEvent",
                        {
                            "item_id": "r1",
                            "pcm": b"\x01\x00",
                            "sample_rate": 24000,
                        },
                    ),
                    (
                        "TranscriptDeltaEvent",
                        {"item_id": "r1", "delta": "Checking."},
                    ),
                ],
                [
                    (
                        "ToolCallEvent",
                        {
                            "item_id": "r1",
                            "non_blocking": True,
                            "tool_call": {
                                "type": "tool_call",
                                "id": "c1",
                                "name": "get_weather",
                                "input": "{}",
                                "state": "pending",
                                "suggested_rules": [],
                                "created_at": AnyString(),
                                "finished_at": None,
                            },
                        },
                    ),
                ],
                [],
                [
                    (
                        "TranscriptDeltaEvent",
                        {"item_id": "r1", "delta": "Sunny."},
                    ),
                    (
                        "ResponseDoneEvent",
                        {
                            "item_id": "r1",
                            "input_tokens": 12,
                            "output_tokens": 7,
                        },
                    ),
                ],
                [],
            ],
        )
        self.assertEqual((model._response_id, model._usage), ("", {}))

    def test_status_only_completion_and_interruption(self) -> None:
        """Root/content statuses finish silent work and reset on barge-in."""
        for at_root in (False, True):
            for interrupted in (False, True):
                with self.subTest(root=at_root, interrupted=interrupted):
                    model = GeminiRealtimeModel(THINKING_MODEL, CRED)
                    content: dict = {"interactionStatus": "IN_PROGRESS"}
                    frame: dict = (
                        content if at_root else {"serverContent": content}
                    )
                    opened = model._parse(frame)
                    reply_id = opened[0].item_id
                    if interrupted:
                        events = model._parse(
                            {"serverContent": {"interrupted": True}},
                        )
                        self.assertEqual(
                            _dump(events),
                            [
                                (
                                    "SpeechStartedEvent",
                                    {
                                        "item_id": AnyString(),
                                        "at_ms": 0,
                                    },
                                ),
                            ],
                        )
                    content = {"interactionStatus": "IDLE"}
                    frame = content if at_root else {"serverContent": content}
                    self.assertEqual(
                        _dump(model._parse(frame)),
                        []
                        if interrupted
                        else [
                            (
                                "ResponseDoneEvent",
                                {
                                    "item_id": reply_id,
                                    "input_tokens": 0,
                                    "output_tokens": 0,
                                },
                            ),
                        ],
                    )
                    self.assertEqual(model._parse(frame), [])
                    next_turn = model._parse(
                        {
                            "serverContent": {
                                "outputTranscription": {"text": "Next"},
                            },
                        },
                    )
                    self.assertEqual(
                        _dump(next_turn),
                        [
                            (
                                "ResponseCreatedEvent",
                                {
                                    "item_id": AnyString(),
                                },
                            ),
                            (
                                "TranscriptDeltaEvent",
                                {
                                    "item_id": next_turn[0].item_id,
                                    "delta": "Next",
                                },
                            ),
                        ],
                    )
                    self.assertNotEqual(next_turn[0].item_id, reply_id)

    def test_same_frame_transcription_and_status(self) -> None:
        """Open status-only responses before input, except on interruption."""
        for at_root in (False, True):
            for interrupted in (False, True):
                with self.subTest(root=at_root, interrupted=interrupted):
                    model = GeminiRealtimeModel(THINKING_MODEL, CRED)
                    content: dict = {
                        "inputTranscription": {"text": "Weather?"},
                        "interrupted": interrupted,
                    }
                    frame: dict = {"serverContent": content}
                    status_target = frame if at_root else content
                    status_target["interactionStatus"] = "IN_PROGRESS"
                    events = model._parse(frame)
                    response_id = model._response_id
                    expected = [
                        (
                            "InputTranscriptionDeltaEvent",
                            {
                                "item_id": model._user_id,
                                "delta": "Weather?",
                                "response_id": response_id,
                            },
                        ),
                    ]
                    if interrupted:
                        self.assertEqual(response_id, "")
                        expected.append(
                            (
                                "SpeechStartedEvent",
                                {"item_id": model._user_id, "at_ms": 0},
                            ),
                        )
                    else:
                        self.assertTrue(response_id)
                        expected.insert(
                            0,
                            ("ResponseCreatedEvent", {"item_id": response_id}),
                        )
                    self.assertEqual(_dump(events), expected)

    def test_same_frame_transcription_and_output(self) -> None:
        """Bind same-frame input to output unless the frame interrupts it."""
        outputs: list[dict] = [
            {"outputTranscription": {"text": "Sunny."}},
            {"modelTurn": {"parts": [{"text": "Sunny."}]}},
            {
                "modelTurn": {
                    "parts": [
                        {
                            "inlineData": {
                                "data": base64.b64encode(b"\x01\x00").decode(),
                            },
                        },
                    ],
                },
            },
        ]
        for output in outputs:
            for interrupted in (False, True):
                with self.subTest(output=output, interrupted=interrupted):
                    model = GeminiRealtimeModel(THINKING_MODEL, CRED)
                    events = model._parse(
                        {
                            "serverContent": {
                                **output,
                                "inputTranscription": {"text": "Weather?"},
                                "interrupted": interrupted,
                            },
                        },
                    )
                    response_id = model._response_id
                    expected = [
                        (
                            "InputTranscriptionDeltaEvent",
                            {
                                "item_id": model._user_id,
                                "delta": "Weather?",
                                "response_id": response_id,
                            },
                        ),
                    ]
                    if interrupted:
                        expected.append(
                            (
                                "SpeechStartedEvent",
                                {"item_id": model._user_id, "at_ms": 0},
                            ),
                        )
                        self.assertEqual(response_id, "")
                    else:
                        self.assertTrue(response_id)
                        expected.insert(
                            0,
                            ("ResponseCreatedEvent", {"item_id": response_id}),
                        )
                        if output == outputs[-1]:
                            expected.append(
                                (
                                    "AudioDeltaEvent",
                                    {
                                        "item_id": response_id,
                                        "pcm": b"\x01\x00",
                                        "sample_rate": 24000,
                                    },
                                ),
                            )
                        else:
                            expected.append(
                                (
                                    "TranscriptDeltaEvent",
                                    {
                                        "item_id": response_id,
                                        "delta": "Sunny.",
                                    },
                                ),
                            )
                    self.assertEqual(_dump(events), expected)

    def test_late_transcription_and_silent_completion(self) -> None:
        """Thinking before transcription and silent replies retain input."""
        for starts_thinking in (False, True):
            with self.subTest(starts_thinking=starts_thinking):
                model = GeminiRealtimeModel(THINKING_MODEL, CRED)
                if starts_thinking:
                    model._parse({"interactionStatus": "IN_PROGRESS"})
                reply_id = model._response_id
                for text in ("Check ", "weather"):
                    events = model._parse(
                        {
                            "serverContent": {
                                "inputTranscription": {"text": text},
                            },
                        },
                    )
                    self.assertEqual(
                        _dump(events),
                        [
                            (
                                "InputTranscriptionDeltaEvent",
                                {
                                    "item_id": model._user_id,
                                    "delta": text,
                                    "response_id": reply_id,
                                },
                            ),
                        ],
                    )
                expected = [
                    (
                        "InputTranscriptionEvent",
                        {
                            "item_id": model._user_id,
                            "text": "Check weather",
                        },
                    ),
                ]
                if starts_thinking:
                    expected.append(
                        (
                            "ResponseDoneEvent",
                            {
                                "item_id": reply_id,
                                "input_tokens": 0,
                                "output_tokens": 0,
                            },
                        ),
                    )
                self.assertEqual(
                    _dump(model._parse({"interactionStatus": "IDLE"})),
                    expected,
                )
                self.assertEqual(
                    (model._input_text, model._user_id, model._response_id),
                    ("", "", ""),
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
                        "non_blocking": False,
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

    async def test_tool_result_state_and_scheduling(self) -> None:
        """All Gemini models encode errors; only Thinking schedules output."""
        names = (
            THINKING_MODEL,
            MODEL,
            "gemini-3.8-live",
            "gemini-2.5-flash-native-audio-preview-12-2025",
        )
        for name, state, silent in product(
            names,
            ToolResultState,
            (False, True),
        ):
            with self.subTest(model=name, state=state, silent=silent):
                model = GeminiRealtimeModel(name, CRED)
                thinking = name == THINKING_MODEL
                model._send = AsyncMock()
                if thinking:
                    model._pending_tool_calls.add("c1")
                block = ToolResultBlock(
                    id="c1",
                    name="weather",
                    output="result",
                    state=state,
                )
                if silent:
                    await model.push_tool_result_silently(block)
                else:
                    await model.push_tool_result(block)
                failed = state in (
                    ToolResultState.ERROR,
                    ToolResultState.DENIED,
                    ToolResultState.INTERRUPTED,
                )
                response: dict = {
                    "id": "c1",
                    "name": "weather",
                    "response": {
                        "error" if failed else "output": "result",
                    },
                }
                if thinking and silent:
                    response["scheduling"] = "SILENT"
                self.assertEqual(
                    {
                        "frames": [
                            call.args[0]
                            for call in model._send.await_args_list
                        ],
                        "awaiting": model._awaiting_tool_reply,
                        "pending": model._pending_tool_calls,
                    },
                    {
                        "frames": [
                            {
                                "toolResponse": {
                                    "functionResponses": [response],
                                },
                            },
                        ],
                        "awaiting": thinking and not silent,
                        "pending": set(),
                    },
                )

    async def test_silent_result_preserves_newer_followup(self) -> None:
        """An old result cannot clear another tool's pending response."""
        model = GeminiRealtimeModel(THINKING_MODEL, CRED)
        model._send = AsyncMock()
        model._awaiting_tool_reply = True
        model._pending_tool_calls.add("new")
        await model.push_tool_result_silently(
            ToolResultBlock(id="old", name="weather", output="done"),
        )
        self.assertEqual(
            {
                "awaiting": model._awaiting_tool_reply,
                "pending": model._pending_tool_calls,
                "frame": model._send.await_args.args[0],
            },
            {
                "awaiting": True,
                "pending": {"new"},
                "frame": {
                    "toolResponse": {
                        "functionResponses": [
                            {
                                "id": "old",
                                "name": "weather",
                                "response": {"output": "done"},
                                "scheduling": "SILENT",
                            },
                        ],
                    },
                },
            },
        )

    async def test_transcription_tracks_first_background_response(
        self,
    ) -> None:
        """A gap after multiple responses still refers to the first one."""
        model = GeminiRealtimeModel(THINKING_MODEL, CRED)
        model._send = AsyncMock()
        first_response = ""
        for call_id in ("c1", "c2"):
            model._parse(
                {
                    "serverContent": {
                        "outputTranscription": {"text": "Checking."},
                    },
                    "toolCall": {
                        "functionCalls": [
                            {
                                "id": call_id,
                                "name": "weather",
                                "args": {},
                            },
                        ],
                    },
                },
            )
            if not first_response:
                first_response = model._response_id
            else:
                self.assertNotEqual(model._response_id, first_response)
            model._parse({"serverContent": {"interactionStatus": "IDLE"}})
            await model.push_tool_result(
                ToolResultBlock(id=call_id, name="weather", output="done"),
            )
        events = model._parse(
            {"serverContent": {"inputTranscription": {"text": "Weather?"}}},
        )
        self.assertEqual(
            _dump(events),
            [
                (
                    "InputTranscriptionDeltaEvent",
                    {
                        "item_id": model._user_id,
                        "delta": "Weather?",
                        "response_id": first_response,
                    },
                ),
            ],
        )

    async def test_transcription_association_cleared_at_boundaries(
        self,
    ) -> None:
        """New input cannot inherit a completed or interrupted interaction."""
        for boundary in (
            "completed",
            "cancel",
            "provider_interrupt",
            "new_turn",
            "text",
            "close",
        ):
            with self.subTest(boundary=boundary):
                model = GeminiRealtimeModel(
                    THINKING_MODEL,
                    CRED,
                    parameters=GeminiRealtimeModel.Parameters(
                        turn_detection="none",
                    ),
                )
                model._send = AsyncMock()
                model._parse(
                    {
                        "serverContent": {
                            "outputTranscription": {"text": "Checking."},
                        },
                        "toolCall": {
                            "functionCalls": [
                                {
                                    "id": "c1",
                                    "name": "weather",
                                    "args": {},
                                },
                            ],
                        },
                    },
                )
                model._parse({"serverContent": {"interactionStatus": "IDLE"}})
                self.assertTrue(model._transcription_response_id)
                if boundary != "close":
                    model._parse(
                        {
                            "serverContent": {
                                "inputTranscription": {
                                    "text": "Old request",
                                },
                            },
                        },
                    )
                old_user_id = model._user_id
                settled = (
                    "InputTranscriptionEvent",
                    {"item_id": old_user_id, "text": "Old request"},
                )
                content: dict = {"inputTranscription": {"text": "Next"}}
                if boundary == "completed":
                    await model.push_tool_result(
                        ToolResultBlock(id="c1", name="weather", output="ok"),
                    )
                    model._parse(
                        {
                            "serverContent": {"interactionStatus": "IDLE"},
                        },
                    )
                elif boundary == "cancel":
                    await model.cancel_response()
                elif boundary == "provider_interrupt":
                    content["interrupted"] = True
                elif boundary == "new_turn":
                    await model.start_turn()
                elif boundary == "text":
                    await model.push_text("New request")
                else:
                    await model.close()
                events = model._parse({"serverContent": content})
                self.assertNotEqual(model._user_id, old_user_id)
                expected = [
                    (
                        "InputTranscriptionDeltaEvent",
                        {
                            "item_id": model._user_id,
                            "delta": "Next",
                            "response_id": "",
                        },
                    ),
                ]
                if boundary == "provider_interrupt":
                    expected.insert(0, settled)
                    expected.append(
                        (
                            "SpeechStartedEvent",
                            {
                                "item_id": model._user_id,
                                "at_ms": 0,
                            },
                        ),
                    )
                self.assertEqual(_dump(events), expected)
                self.assertEqual(model._transcription_response_id, "")
                queued = []
                while not model._queue.empty():
                    queued.append(model._queue.get_nowait())
                self.assertEqual(
                    _dump(queued),
                    [settled]
                    if boundary in ("cancel", "new_turn", "text")
                    else [],
                )

    async def test_local_cancel_settles_thinking_input(self) -> None:
        """Local VAD interruption must separate successive input turns."""
        model = GeminiRealtimeModel(
            THINKING_MODEL,
            CRED,
            parameters=GeminiRealtimeModel.Parameters(turn_detection="none"),
        )
        model._send = AsyncMock()
        setup = model._setup("", None, "")["setup"]
        self.assertEqual(
            setup["realtimeInputConfig"]["activityHandling"],
            "NO_INTERRUPTION",
        )
        first = model._parse(
            {
                "serverContent": {
                    "inputTranscription": {"text": "Check weather"},
                    "interactionStatus": "IN_PROGRESS",
                },
            },
        )
        user_id = first[1].item_id
        await model.cancel_response()
        await model.cancel_response()
        settled = []
        while not model._queue.empty():
            settled.append(model._queue.get_nowait())
        self.assertEqual(
            _dump(settled),
            [
                (
                    "InputTranscriptionEvent",
                    {"item_id": user_id, "text": "Check weather"},
                ),
            ],
        )
        second = model._parse(
            {"serverContent": {"inputTranscription": {"text": "Change "}}},
        )
        await model.cancel_response()
        self.assertTrue(model._queue.empty())
        stale = model._parse({"serverContent": {"interactionStatus": "IDLE"}})
        self.assertEqual(
            _dump(stale),
            [
                (
                    "ResponseDoneEvent",
                    {
                        "item_id": first[0].item_id,
                        "input_tokens": 0,
                        "output_tokens": 0,
                    },
                ),
            ],
        )
        self.assertEqual(
            model._parse({"serverContent": {"interactionStatus": "IDLE"}}),
            [],
        )
        self.assertEqual(
            (model._user_id, model._input_text),
            (second[0].item_id, "Change "),
        )
        second += model._parse(
            {"serverContent": {"inputTranscription": {"text": "city"}}},
        )
        self.assertNotEqual(second[0].item_id, user_id)
        self.assertEqual(
            _dump(second),
            [
                (
                    "InputTranscriptionDeltaEvent",
                    {
                        "item_id": second[0].item_id,
                        "delta": text,
                        "response_id": "",
                    },
                )
                for text in ("Change ", "city")
            ],
        )
        opened = model._parse(
            {"serverContent": {"interactionStatus": "IN_PROGRESS"}},
        )
        self.assertEqual(
            _dump(
                model._parse(
                    {"serverContent": {"interactionStatus": "IDLE"}},
                ),
            ),
            [
                (
                    "InputTranscriptionEvent",
                    {"item_id": second[0].item_id, "text": "Change city"},
                ),
                (
                    "ResponseDoneEvent",
                    {
                        "item_id": opened[0].item_id,
                        "input_tokens": 0,
                        "output_tokens": 0,
                    },
                ),
            ],
        )
        model._send.assert_not_awaited()

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

    async def test_thinking_endpoint_and_reconnect(self) -> None:
        """Thinking uses alpha; ordinary Live retains its beta endpoint."""
        for name, version in (
            (THINKING_MODEL, "v1alpha"),
            ("custom-thinking-alias", "v1alpha"),
            (MODEL, "v1beta"),
        ):
            with self.subTest(model=name):
                original = THINKING_MODEL if version == "v1alpha" else MODEL
                card = GeminiRealtimeModel(original, CRED).card.model_copy(
                    update={"name": name},
                )
                model = GeminiRealtimeModel(name, CRED, model_card=card)
                socket = AsyncMock()

                async def acknowledge(
                    session: GeminiRealtimeModel = model,
                ) -> None:
                    """Acknowledge setup and keep the reader alive."""
                    session._ready.set()
                    await asyncio.Event().wait()

                with patch(
                    "websockets.connect",
                    new_callable=AsyncMock,
                    return_value=socket,
                ) as connect, patch.object(model, "_read", acknowledge):
                    model._response_id = "old-response"
                    model._transcription_response_id = "old-interaction"
                    model._usage = {"responseTokenCount": 99}
                    model._awaiting_tool_reply = True
                    model._pending_tool_calls.add("old-call")
                    await model.connect("hi")
                    self.assertEqual(
                        (
                            model._response_id,
                            model._transcription_response_id,
                            model._usage,
                            model._awaiting_tool_reply,
                            model._pending_tool_calls,
                        ),
                        ("", "", {}, False, set()),
                    )
                    connect.assert_awaited_once_with(
                        "wss://generativelanguage.googleapis.com/ws/"
                        f"google.ai.generativelanguage.{version}."
                        "GenerativeService.BidiGenerateContent?key=key-x",
                    )
                    await model.close()
                    socket.close.assert_awaited_once_with()

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
