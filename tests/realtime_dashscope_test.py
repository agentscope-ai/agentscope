# -*- coding: utf-8 -*-
"""Unit tests for the DashScope realtime adapters: cards, session config
and frame parsing. Nothing here opens a connection."""
# pylint: disable=protected-access
import base64
import unittest
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.credential import DashScopeCredential
from agentscope.realtime import (
    DashScopeAudioRealtimeModel,
    DashScopeRealtimeModel,
    ModelDisconnectedError,
    TruncationSupport,
)
from agentscope.realtime import _events as me

CRED = DashScopeCredential(api_key="sk-x")
TRANSCRIPTION_DONE = "conversation.item.input_audio_transcription.completed"
AMBIENT_DELTA = "conversation.item.ambient_audio_transcription.delta"


class DashScopeCardsTest(unittest.TestCase):
    """Each adapter lists only its own cards, tagged with its type."""

    def test_omni_cards(self) -> None:
        """The Omni adapter lists the four Omni cards."""
        self.assertListEqual(
            [
                (c.name, c.model_type, c.supports_tools, c.max_audio_turns)
                for c in DashScopeRealtimeModel.list_models()
            ],
            [
                (
                    "qwen-omni-turbo-realtime",
                    "dashscope_omni_realtime",
                    False,
                    None,
                ),
                (
                    "qwen3-omni-flash-realtime",
                    "dashscope_omni_realtime",
                    False,
                    8,
                ),
                (
                    "qwen3.5-omni-flash-realtime",
                    "dashscope_omni_realtime",
                    True,
                    80,
                ),
                (
                    "qwen3.5-omni-plus-realtime",
                    "dashscope_omni_realtime",
                    True,
                    100,
                ),
            ],
        )

    def test_audio_cards(self) -> None:
        """The Audio adapter lists the two Audio cards."""
        self.assertListEqual(
            [
                (
                    c.name,
                    c.model_type,
                    c.max_audio_turns,
                    c.max_audio_duration_s,
                )
                for c in DashScopeAudioRealtimeModel.list_models()
            ],
            [
                (
                    "qwen-audio-3.0-realtime-flash",
                    "dashscope_audio_realtime",
                    50,
                    300,
                ),
                (
                    "qwen-audio-3.0-realtime-plus",
                    "dashscope_audio_realtime",
                    50,
                    300,
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
                "qwen-audio-3.0-realtime-flash": "DashScopeAudioRealtimeModel",
                "qwen-audio-3.0-realtime-plus": "DashScopeAudioRealtimeModel",
                "qwen-omni-turbo-realtime": "DashScopeRealtimeModel",
                "qwen3-omni-flash-realtime": "DashScopeRealtimeModel",
                "qwen3.5-omni-flash-realtime": "DashScopeRealtimeModel",
                "qwen3.5-omni-plus-realtime": "DashScopeRealtimeModel",
            },
        )

    def test_unknown_model_name_is_rejected(self) -> None:
        """A name with no card fails at construction."""
        with self.assertRaises(ValueError):
            DashScopeRealtimeModel("no-such-model", CRED)


class DashScopeSessionUpdateTest(unittest.TestCase):
    """The session.update payload each adapter sends on connect."""

    def test_omni_payload(self) -> None:
        """Omni session.update with tools and transcription."""
        model = DashScopeRealtimeModel("qwen3.5-omni-flash-realtime", CRED)
        self.assertDictEqual(
            model._session_update("be nice", [{"type": "function"}]),
            {
                "type": "session.update",
                "session": {
                    "instructions": "be nice",
                    "modalities": ["audio", "text"],
                    "voice": "Cherry",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm24",
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "silence_duration_ms": 800,
                    },
                    "input_audio_transcription": {
                        "model": "gummy-realtime-v1",
                    },
                    "tools": [{"type": "function"}],
                },
            },
        )

    def test_omni_withholds_tools_the_card_does_not_support(self) -> None:
        """Tools are not sent for a model whose card declines them."""
        model = DashScopeRealtimeModel("qwen3-omni-flash-realtime", CRED)
        session = model._session_update("x", [{"type": "function"}])["session"]
        self.assertNotIn("tools", session)

    def test_audio_payload_with_smart_turn(self) -> None:
        """Audio session.update with smart_turn and a voiceprint."""
        model = DashScopeAudioRealtimeModel(
            "qwen-audio-3.0-realtime-plus",
            CRED,
            parameters=DashScopeAudioRealtimeModel.Parameters(
                turn_detection="smart_turn",
                voiceprint_audio_urls=["https://x/a.wav"],
                max_history_turns=30,
            ),
        )
        self.assertDictEqual(
            model._session_update("be nice", None),
            {
                "type": "session.update",
                "session": {
                    "instructions": "be nice",
                    "modalities": ["audio", "text"],
                    "voice": "longanqian",
                    "input_audio_format": "pcm",
                    "output_audio_format": "pcm",
                    "max_history_turns": 30,
                    "turn_detection": {
                        "type": "smart_turn",
                        "voiceprint_audio_urls": ["https://x/a.wav"],
                    },
                },
            },
        )

    def test_turn_detection_none_hands_endpointing_to_caller(self) -> None:
        """``none`` sends null and marks endpointing as ours."""
        model = DashScopeAudioRealtimeModel(
            "qwen-audio-3.0-realtime-flash",
            CRED,
            parameters=DashScopeAudioRealtimeModel.Parameters(
                turn_detection="none",
            ),
        )
        session = model._session_update("x", None)["session"]
        self.assertIsNone(session["turn_detection"])

    def test_adapter_facts(self) -> None:
        """Protocol facts differ per adapter, not per model."""
        omni = DashScopeRealtimeModel("qwen3.5-omni-flash-realtime", CRED)
        audio = DashScopeAudioRealtimeModel(
            "qwen-audio-3.0-realtime-plus",
            CRED,
        )
        self.assertListEqual(
            [
                (omni.truncation, omni.supports_text_input),
                (audio.truncation, audio.supports_text_input),
            ],
            [
                (TruncationSupport.NONE, False),
                (TruncationSupport.NONE, True),
            ],
        )


class DashScopeParseTest(unittest.TestCase):
    """Server frames -> model events."""

    def setUp(self) -> None:
        """Open a response so deltas have an item to attach to."""
        self.model = DashScopeRealtimeModel(
            "qwen3.5-omni-flash-realtime",
            CRED,
        )
        self.model._parse(
            {"type": "response.created", "response": {"id": "r1"}},
        )

    def test_reply_frames(self) -> None:
        """Every reply-side frame maps to one model event."""
        frames = [
            {"type": "response.audio_transcript.delta", "delta": "你好"},
            {
                "type": "response.audio.delta",
                "delta": base64.b64encode(b"\x01\x00").decode(),
            },
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "u1",
                "audio_start_ms": 120,
            },
            {
                "type": TRANSCRIPTION_DONE,
                "item_id": "u1",
                "transcript": "天气",
            },
            {
                "type": "response.done",
                "response": {
                    "id": "r1",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
            {"type": "session.updated"},
        ]
        self.assertListEqual(
            [self.model._parse(f) for f in frames],
            [
                me.TranscriptDeltaEvent(item_id="r1", delta="你好"),
                me.AudioDeltaEvent(
                    item_id="r1",
                    pcm=b"\x01\x00",
                    sample_rate=24000,
                ),
                me.SpeechStartedEvent(item_id="u1", at_ms=120),
                me.InputTranscriptionEvent(item_id="u1", text="天气"),
                me.ResponseDoneEvent(
                    item_id="r1",
                    input_tokens=10,
                    output_tokens=5,
                ),
                None,
            ],
        )

    def test_tool_call_done_frame_is_authoritative(self) -> None:
        """Accumulated deltas are only a fallback for a done frame that
        omits ``arguments``."""
        self.model._parse(
            {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "get_weather",
                },
            },
        )
        self.model._parse(
            {
                "type": "response.function_call_arguments.delta",
                "call_id": "c1",
                "delta": '{"city":',
            },
        )
        with_args = self.model._parse(
            {
                "type": "response.function_call_arguments.done",
                "call_id": "c1",
                "arguments": '{"city":"sh"}',
            },
        )
        self.model._parse(
            {
                "type": "response.function_call_arguments.delta",
                "call_id": "c2",
                "name": "f",
                "delta": '{"a":1}',
            },
        )
        without_args = self.model._parse(
            {"type": "response.function_call_arguments.done", "call_id": "c2"},
        )

        self.assertListEqual(
            [
                (with_args.tool_call.name, with_args.tool_call.input),
                (without_args.tool_call.name, without_args.tool_call.input),
            ],
            [("get_weather", '{"city":"sh"}'), ("f", '{"a":1}')],
        )

    def test_audio_specific_frames_are_known_and_dropped(self) -> None:
        """Audio-only frames are recognised and dropped, errors kept."""
        model = DashScopeAudioRealtimeModel(
            "qwen-audio-3.0-realtime-plus",
            CRED,
        )
        self.assertListEqual(
            [
                model._parse(
                    {
                        "type": AMBIENT_DELTA,
                        "delta": "x",
                    },
                ),
                model._parse({"type": "voiceprint_audio_list.completed"}),
                model._parse(
                    {
                        "type": "error",
                        "error": {"code": "E1", "message": "boom"},
                    },
                ),
            ],
            [None, None, me.ModelErrorEvent(code="E1", message="boom")],
        )


class DashScopeAudioTextInputTest(IsolatedAsyncioTestCase):
    """push_text sends a text item and asks for the reply."""

    async def test_push_text_wire_shape(self) -> None:
        """A text turn is one item plus a response request."""
        model = DashScopeAudioRealtimeModel(
            "qwen-audio-3.0-realtime-plus",
            CRED,
        )
        sent: list[dict] = []

        async def capture(payload: dict) -> None:
            sent.append(payload)

        model._send = capture  # type: ignore[method-assign]
        await model.push_text("你好")

        self.assertListEqual(
            sent,
            [
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "你好"}],
                    },
                },
                {
                    "type": "response.create",
                    "response": {"modalities": ["text", "audio"]},
                },
            ],
        )


class DashScopeDisconnectTest(IsolatedAsyncioTestCase):
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

        model = DashScopeRealtimeModel("qwen3.5-omni-flash-realtime", CRED)
        model._ws = ClosedSocket()

        with self.assertRaises(ModelDisconnectedError) as ctx:
            await model.push_audio(b"\x00\x00")
        self.assertEqual(
            (str(ctx.exception), model._ws),
            (
                "1007 (invalid frame payload data) idle 180s",
                None,
            ),
        )

    async def test_send_before_connect(self) -> None:
        """No socket at all is the same condition."""
        model = DashScopeRealtimeModel("qwen3.5-omni-flash-realtime", CRED)
        with self.assertRaises(ModelDisconnectedError):
            await model.commit_turn()
