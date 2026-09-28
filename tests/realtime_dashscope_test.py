# -*- coding: utf-8 -*-
"""Unit tests for the DashScope realtime adapters: cards, session config
and frame parsing. Nothing here opens a connection."""
# pylint: disable=protected-access
import asyncio
import base64
import json
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, call, patch

from agentscope.credential import DashScopeCredential
from agentscope.realtime import (
    DashScopeAudioRealtimeModel,
    DashScopeRealtimeModel,
    ModelDisconnectedError,
    TruncationSupport,
)
from agentscope.realtime import _events as me
from agentscope.message import (
    AssistantMsg,
    SystemMsg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
)

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
        """The Audio adapter lists the three Audio cards."""
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
                (
                    "qwen-audio-3.1-realtime-plus",
                    "dashscope_audio_realtime",
                    50,
                    300,
                ),
            ],
        )

    def test_omni_cards_apply_model_specific_voice_defaults(self) -> None:
        """Card defaults drive runtime parameters unless explicitly set."""
        cards = {
            card.name: card for card in DashScopeRealtimeModel.list_models()
        }
        self.assertDictEqual(
            {
                name: {
                    "schema": cards[name].parameter_schema["properties"][
                        "voice"
                    ],
                    "runtime_default": DashScopeRealtimeModel(
                        name,
                        CRED,
                    ).parameters.voice,
                }
                for name in cards
            },
            {
                "qwen-omni-turbo-realtime": {
                    "schema": {
                        "default": "Chelsie",
                        "enum": ["Cherry", "Serena", "Ethan", "Chelsie"],
                        "title": "Voice",
                        "type": "string",
                    },
                    "runtime_default": "Chelsie",
                },
                "qwen3-omni-flash-realtime": {
                    "schema": {
                        "default": "Cherry",
                        "enum": ["Cherry", "Serena", "Ethan", "Chelsie"],
                        "title": "Voice",
                        "type": "string",
                    },
                    "runtime_default": "Cherry",
                },
                "qwen3.5-omni-flash-realtime": {
                    "schema": {
                        "default": "Tina",
                        "enum": ["Tina", "Serena", "Ethan"],
                        "title": "Voice",
                        "type": "string",
                    },
                    "runtime_default": "Tina",
                },
                "qwen3.5-omni-plus-realtime": {
                    "schema": {
                        "default": "Tina",
                        "enum": ["Tina", "Serena", "Ethan"],
                        "title": "Voice",
                        "type": "string",
                    },
                    "runtime_default": "Tina",
                },
            },
        )

        explicit = DashScopeRealtimeModel(
            "qwen3.5-omni-flash-realtime",
            CRED,
            parameters=DashScopeRealtimeModel.Parameters(voice="Serena"),
        )
        self.assertEqual(explicit.parameters.voice, "Serena")

    def test_audio_cards_apply_model_specific_voice_defaults(self) -> None:
        """Audio cards expose and apply their complete voice schemas."""
        cards = {
            card.name: card
            for card in DashScopeAudioRealtimeModel.list_models()
        }
        self.assertDictEqual(
            {
                name: {
                    "schema": cards[name].parameter_schema["properties"][
                        "voice"
                    ],
                    "runtime_default": DashScopeAudioRealtimeModel(
                        name,
                        CRED,
                    ).parameters.voice,
                }
                for name in cards
            },
            {
                "qwen-audio-3.0-realtime-flash": {
                    "schema": {
                        "default": "longanqian",
                        "enum": [
                            "longanqian",
                            "longanlingxin",
                            "longanlingxi",
                            "longanxiaoxin",
                            "longanlufeng",
                        ],
                        "title": "Voice",
                        "type": "string",
                    },
                    "runtime_default": "longanqian",
                },
                "qwen-audio-3.0-realtime-plus": {
                    "schema": {
                        "default": "longanqian",
                        "enum": [
                            "longanqian",
                            "longanlingxin",
                            "longanlingxi",
                            "longanxiaoxin",
                            "longanlufeng",
                        ],
                        "title": "Voice",
                        "type": "string",
                    },
                    "runtime_default": "longanqian",
                },
                "qwen-audio-3.1-realtime-plus": {
                    "schema": {
                        "default": "longanqian_v3.1",
                        "enum": [
                            "longanqian",
                            "longanlingxin",
                            "longanlingxi",
                            "longanxiaoxin",
                            "longanlufeng",
                            "longanqian_v3.1",
                            "longanhuan_v3.1",
                            "longanlingxin_v3.1",
                            "longanfengyue_v3.1",
                            "xunanchuan_v3.1",
                            "beth_v3.1",
                            "betty_v3.1",
                            "cally_v3.1",
                        ],
                        "title": "Voice",
                        "type": "string",
                    },
                    "runtime_default": "longanqian_v3.1",
                },
            },
        )

    def test_credential_builds_regional_realtime_urls(self) -> None:
        """Realtime URLs preserve the configured HTTP endpoint host."""
        self.assertListEqual(
            [
                DashScopeCredential(
                    api_key="sk-x",
                ).get_realtime_base_url(),
                DashScopeCredential(
                    api_key="sk-x",
                    base_url=(
                        "https://dashscope-intl.aliyuncs.com"
                        "/compatible-mode/v1"
                    ),
                ).get_realtime_base_url(),
                DashScopeCredential(
                    api_key="sk-x",
                    base_url=(
                        "https://llm-beijing.cn-beijing.maas.aliyuncs.com"
                        "/compatible-mode/v1"
                    ),
                ).get_realtime_base_url(),
                DashScopeCredential(
                    api_key="sk-x",
                    base_url=("http://localhost:8080/custom/dashscope/path"),
                ).get_realtime_base_url(),
            ],
            [
                "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
                "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime",
                "wss://llm-beijing.cn-beijing.maas.aliyuncs.com"
                "/api-ws/v1/realtime",
                "ws://localhost:8080/api-ws/v1/realtime",
            ],
        )

        with self.assertRaisesRegex(ValueError, "Invalid DashScope"):
            DashScopeCredential(
                api_key="sk-x",
                base_url="not-a-url",
            ).get_realtime_base_url()

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
                "qwen-audio-3.1-realtime-plus": "DashScopeAudioRealtimeModel",
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
                    "voice": "Tina",
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

    def test_audio_31_payload_uses_model_voice_default(self) -> None:
        """Audio 3.1 sends its model-specific default voice."""
        model = DashScopeAudioRealtimeModel(
            "qwen-audio-3.1-realtime-plus",
            CRED,
        )
        self.assertDictEqual(
            model._session_update("be nice", None),
            {
                "type": "session.update",
                "session": {
                    "instructions": "be nice",
                    "modalities": ["audio", "text"],
                    "voice": "longanqian_v3.1",
                    "input_audio_format": "pcm",
                    "output_audio_format": "pcm",
                    "max_history_turns": 20,
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "silence_duration_ms": 800,
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
                (
                    omni.truncation,
                    omni.supports_text_input,
                    omni.supports_history_replay,
                ),
                (
                    audio.truncation,
                    audio.supports_text_input,
                    audio.supports_history_replay,
                ),
            ],
            [
                (TruncationSupport.NONE, False, False),
                (TruncationSupport.NONE, True, True),
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

    async def test_history_replay_preserves_roles_and_tool_order(self) -> None:
        """History items are inserted silently and incomplete tools drop."""
        model = DashScopeAudioRealtimeModel(
            "qwen-audio-3.0-realtime-plus",
            CRED,
        )
        sent: list[dict] = []

        async def capture(payload: dict) -> None:
            sent.append(payload)

        model._send = capture  # type: ignore[method-assign]
        model._session_ready.set()
        await model.replay_history(
            [
                SystemMsg(name="summary", content="Earlier summary"),
                UserMsg(name="user", content="Weather?"),
                AssistantMsg(
                    name="assistant",
                    content=[
                        TextBlock(text="Let me check."),
                        ToolCallBlock(
                            id="call-1",
                            name="weather",
                            input='{"city":"Shanghai"}',
                        ),
                        ToolResultBlock(
                            id="call-1",
                            name="weather",
                            output="sunny",
                            state="success",
                        ),
                        TextBlock(text="It is sunny."),
                        ToolCallBlock(
                            id="call-unfinished",
                            name="other",
                            input="{}",
                        ),
                    ],
                ),
            ],
        )

        self.assertListEqual(
            sent,
            [
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "system",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Earlier summary",
                            },
                        ],
                    },
                },
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Weather?"},
                        ],
                    },
                },
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Let me check.",
                            },
                        ],
                    },
                },
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "weather",
                        "arguments": '{"city":"Shanghai"}',
                    },
                },
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": "call-1",
                        "output": "sunny",
                    },
                },
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "It is sunny.",
                            },
                        ],
                    },
                },
            ],
        )

    async def test_history_replay_waits_for_session_update(self) -> None:
        """No history item is sent before the session config is active."""
        model = DashScopeAudioRealtimeModel(
            "qwen-audio-3.0-realtime-plus",
            CRED,
        )
        sent: list[dict] = []

        async def capture(payload: dict) -> None:
            sent.append(payload)

        model._send = capture  # type: ignore[method-assign]
        replay = asyncio.create_task(
            model.replay_history([UserMsg(name="user", content="hello")]),
        )
        await asyncio.sleep(0)
        self.assertFalse(replay.done())
        self.assertListEqual(sent, [])

        self.assertIsNone(model._parse({"type": "session.updated"}))
        await replay
        self.assertListEqual(
            sent,
            [
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "hello"},
                        ],
                    },
                },
            ],
        )

    async def test_history_replay_surfaces_session_setup_error(self) -> None:
        """A rejected session update fails replay without a timeout."""
        model = DashScopeAudioRealtimeModel(
            "qwen-audio-3.0-realtime-plus",
            CRED,
        )
        sent: list[dict] = []

        async def capture(payload: dict) -> None:
            sent.append(payload)

        model._send = capture  # type: ignore[method-assign]
        replay = asyncio.create_task(
            model.replay_history([UserMsg(name="user", content="hello")]),
        )
        await asyncio.sleep(0)

        event = model._parse(
            {
                "type": "error",
                "error": {
                    "code": "invalid_request",
                    "message": "bad session config",
                },
            },
        )

        self.assertEqual(
            event,
            me.ModelErrorEvent(
                code="invalid_request",
                message="bad session config",
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "bad session config"):
            await asyncio.wait_for(replay, timeout=0.1)
        self.assertListEqual(sent, [])


class DashScopeDisconnectTest(IsolatedAsyncioTestCase):
    """A closed WebSocket surfaces as ModelDisconnectedError."""

    async def test_workspace_endpoint_connects_models(self) -> None:
        """Both adapters connect through the credential endpoint."""

        class ReadySocket:
            """A socket that confirms setup and then remains open."""

            def __init__(self) -> None:
                self.ready_sent = False

            def __aiter__(self) -> "ReadySocket":
                """Return this socket as an asynchronous iterator."""
                return self

            async def __anext__(self) -> str:
                """Confirm setup once, then wait for cancellation."""
                if not self.ready_sent:
                    self.ready_sent = True
                    return json.dumps({"type": "session.updated"})
                await asyncio.Future()
                raise StopAsyncIteration

            async def send(self, _payload: str) -> None:
                """Accept an outgoing frame."""

            async def close(self) -> None:
                """Accept connection shutdown."""

        credential = DashScopeCredential(
            api_key="sk-workspace",
            base_url=(
                "https://llm-workspace.ap-southeast-1.maas.aliyuncs.com"
                "/compatible-mode/v1"
            ),
        )
        omni = DashScopeRealtimeModel(
            "qwen3.5-omni-plus-realtime",
            credential,
        )
        audio = DashScopeAudioRealtimeModel(
            "qwen-audio-3.1-realtime-plus",
            credential,
        )
        connect = AsyncMock(
            side_effect=[ReadySocket(), ReadySocket()],
        )

        with patch("websockets.connect", new=connect):
            await omni.connect(instructions="test")
            await audio.connect(instructions="test")

        await omni.close()
        await audio.close()
        base_url = (
            "wss://llm-workspace.ap-southeast-1.maas.aliyuncs.com"
            "/api-ws/v1/realtime"
        )
        headers = {
            "Authorization": "Bearer sk-workspace",
            "X-DashScope-DataInspection": "disable",
        }
        self.assertListEqual(
            connect.await_args_list,
            [
                call(
                    f"{base_url}?model=qwen3.5-omni-plus-realtime",
                    additional_headers=headers,
                ),
                call(
                    f"{base_url}?model=qwen-audio-3.1-realtime-plus",
                    additional_headers=headers,
                ),
            ],
        )

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

    async def test_reconnect_replaces_the_previous_event_queue(self) -> None:
        """Termination markers from an old socket cannot end a new one."""

        class LiveSocket:
            """A socket that remains open until its reader is cancelled."""

            def __init__(self) -> None:
                self.closed = False
                self.sent: list[str] = []
                self.ready_sent = False

            def __aiter__(self) -> "LiveSocket":
                """Return the socket as its own frame iterator."""
                return self

            async def __anext__(self) -> str:
                """Wait until the reader task is cancelled."""
                if not self.ready_sent:
                    self.ready_sent = True
                    return json.dumps({"type": "session.updated"})
                await asyncio.Future()
                raise StopAsyncIteration

            async def send(self, payload: str) -> None:
                """Capture an outgoing WebSocket frame."""
                self.sent.append(payload)

            async def close(self) -> None:
                """Record that the socket was closed."""
                self.closed = True

        model = DashScopeRealtimeModel(
            "qwen3.5-omni-flash-realtime",
            CRED,
        )
        old_queue = model._queue
        old_queue.put_nowait(me.SessionEndedEvent(reason="closed"))
        old_queue.put_nowait(None)
        socket = LiveSocket()

        with patch(
            "websockets.connect",
            new=AsyncMock(return_value=socket),
        ):
            await model.connect(instructions="test")

        connection_state = {
            "queue_replaced": model._queue is not old_queue,
            "new_queue_size": model._queue.qsize(),
            "old_queue_size": old_queue.qsize(),
            "sent": [json.loads(payload) for payload in socket.sent],
        }
        await asyncio.sleep(0)
        await model.close()

        self.assertDictEqual(
            {
                "connected": connection_state,
                "closed": {
                    "socket": socket.closed,
                    "reader": model._reader,
                    "websocket": model._ws,
                },
            },
            {
                "connected": {
                    "queue_replaced": True,
                    "new_queue_size": 0,
                    "old_queue_size": 0,
                    "sent": [
                        {
                            "type": "session.update",
                            "session": {
                                "instructions": "test",
                                "modalities": ["audio", "text"],
                                "voice": "Tina",
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
                            },
                        },
                    ],
                },
                "closed": {
                    "socket": True,
                    "reader": None,
                    "websocket": None,
                },
            },
        )

    async def test_connect_surfaces_session_setup_error(self) -> None:
        """An invalid session update fails connect with provider detail."""

        class RejectedSocket:
            """Return one provider error, then wait for cancellation."""

            def __init__(self) -> None:
                self.error_sent = False
                self.closed = False

            def __aiter__(self) -> "RejectedSocket":
                """Return this socket as an asynchronous iterator."""
                return self

            async def __anext__(self) -> str:
                """Return the provider error and then wait indefinitely."""
                if not self.error_sent:
                    self.error_sent = True
                    return json.dumps(
                        {
                            "type": "error",
                            "error": {
                                "code": "invalid_parameter",
                                "message": "Voice 'Cherry' is not supported.",
                            },
                        },
                    )
                await asyncio.Future()
                raise StopAsyncIteration

            async def send(self, _payload: str) -> None:
                """Accept the session update sent during connection setup."""
                return None

            async def close(self) -> None:
                """Record that the socket was closed."""
                self.closed = True

        socket = RejectedSocket()
        model = DashScopeRealtimeModel(
            "qwen3.5-omni-flash-realtime",
            CRED,
            parameters=DashScopeRealtimeModel.Parameters(voice="Cherry"),
        )

        with patch(
            "websockets.connect",
            new=AsyncMock(return_value=socket),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Voice 'Cherry' is not supported",
            ):
                await model.connect(instructions="test")

        self.assertDictEqual(
            {
                "closed": socket.closed,
                "reader": model._reader,
                "websocket": model._ws,
            },
            {
                "closed": True,
                "reader": None,
                "websocket": None,
            },
        )
