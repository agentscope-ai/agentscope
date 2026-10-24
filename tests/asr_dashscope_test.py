# -*- coding: utf-8 -*-
"""DashScope batch and realtime ASR model tests."""

import asyncio
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from agentscope.asr import DashScopeASRModel, DashScopeRealtimeASRModel
from agentscope.credential import DashScopeCredential


class DashScopeASRModelTest(IsolatedAsyncioTestCase):
    """Verify DashScope request mapping and response parsing."""

    async def test_transcribes_browser_audio(self) -> None:
        """Encode the recording and call the non-streaming native API."""
        model = DashScopeASRModel(
            credential=DashScopeCredential(api_key="test-key"),
            parameters=DashScopeASRModel.Parameters(
                language_hints=["zh", "en"],
                keep_dialect=True,
                sample_rate=48000,
            ),
        )
        response = SimpleNamespace(
            status_code=200,
            output={"text": "你好，AgentScope。"},
        )

        with patch(
            "dashscope.MultiModalConversation.call",
            return_value=response,
        ) as call:
            result = await model.transcribe(
                audio=b"browser audio",
                filename="recording.webm",
            )

        self.assertEqual(result.content.text, "你好，AgentScope。")
        call.assert_called_once_with(
            api_key="test-key",
            base_address="https://dashscope.aliyuncs.com/api/v1",
            model="qwen-audio-3.1-asr-flash",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": (
                                    "data:audio/webm;base64,"
                                    "YnJvd3NlciBhdWRpbw=="
                                ),
                            },
                        },
                    ],
                },
            ],
            stream=False,
            language_hints=["zh", "en"],
            keep_dialect=True,
            speaker_diarization_enabled=False,
            sample_rate=48000,
            format="webm",
        )

    async def test_rejects_invalid_audio(self) -> None:
        """Validate content and filename before contacting DashScope."""
        model = DashScopeASRModel(
            credential=DashScopeCredential(api_key="test-key"),
        )

        with self.assertRaisesRegex(ValueError, "audio must not be empty"):
            await model.transcribe(audio=b"", filename="recording.wav")
        with self.assertRaisesRegex(ValueError, "Unsupported audio"):
            await model.transcribe(audio=b"audio", filename="recording.txt")

    async def test_omits_parameters_unsupported_by_qwen_audio_3_0(
        self,
    ) -> None:
        """Do not send the user-facing 3.1 switches to the 3.0 model."""
        model = DashScopeASRModel(
            credential=DashScopeCredential(api_key="test-key"),
            model="qwen-audio-3.0-asr-flash",
            parameters=DashScopeASRModel.Parameters(
                keep_dialect=True,
                speaker_diarization_enabled=True,
            ),
        )
        response = SimpleNamespace(status_code=200, output={"text": "text"})

        with patch(
            "dashscope.MultiModalConversation.call",
            return_value=response,
        ) as call:
            await model.transcribe(audio=b"audio", filename="audio.wav")

        call.assert_called_once_with(
            api_key="test-key",
            base_address="https://dashscope.aliyuncs.com/api/v1",
            model="qwen-audio-3.0-asr-flash",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": "data:audio/wav;base64,YXVkaW8=",
                            },
                        },
                    ],
                },
            ],
            stream=False,
            format="wav",
        )

    async def test_surfaces_provider_error(self) -> None:
        """Raise a useful error when the provider rejects the request."""
        model = DashScopeASRModel(
            credential=DashScopeCredential(api_key="test-key"),
        )
        response = SimpleNamespace(
            status_code=400,
            output=None,
            message="bad request",
        )

        with patch(
            "dashscope.MultiModalConversation.call",
            return_value=response,
        ):
            with self.assertRaisesRegex(RuntimeError, "DashScope ASR API"):
                await model.transcribe(audio=b"audio", filename="audio.wav")


class DashScopeASRModelCardTest(TestCase):
    """Verify credential discovery for supported ASR models."""

    def test_lists_latest_model(self) -> None:
        """Expose Qwen Audio 3.0, 3.1, and streaming models."""
        cards = DashScopeCredential.list_asr_models()

        self.assertEqual(
            [card.model_dump(exclude={"parameter_schema"}) for card in cards],
            [
                {
                    "type": "asr_model",
                    "name": "qwen-audio-3.0-asr-flash",
                    "label": "Qwen Audio 3.0 ASR Flash",
                    "input_types": [
                        "audio/aac",
                        "audio/flac",
                        "audio/mpeg",
                        "audio/mp4",
                        "audio/ogg",
                        "audio/opus",
                        "audio/wav",
                        "audio/webm",
                    ],
                    "output_types": ["text/plain"],
                    "realtime": False,
                    "parameters_overrides": {
                        "language_hints": {"hidden": True},
                        "keep_dialect": {"hidden": True},
                        "speaker_diarization_enabled": {"hidden": True},
                        "vocabulary": {"hidden": True},
                        "sample_rate": {"hidden": True},
                    },
                },
                {
                    "type": "asr_model",
                    "name": "qwen-audio-3.1-asr-flash",
                    "label": "Qwen Audio 3.1 ASR Flash",
                    "input_types": [
                        "audio/aac",
                        "audio/flac",
                        "audio/mpeg",
                        "audio/mp4",
                        "audio/ogg",
                        "audio/opus",
                        "audio/wav",
                        "audio/webm",
                    ],
                    "output_types": ["text/plain"],
                    "realtime": False,
                    "parameters_overrides": {
                        "language_hints": {"hidden": True},
                        "vocabulary": {"hidden": True},
                        "sample_rate": {"hidden": True},
                    },
                },
                {
                    "type": "asr_model",
                    "name": "qwen-audio-3.1-asr-flash-streaming",
                    "label": "Qwen Audio 3.1 ASR Flash Streaming",
                    "input_types": ["audio/pcm"],
                    "output_types": ["text/plain"],
                    "realtime": True,
                    "parameters_overrides": {
                        "sample_rate": {"hidden": True},
                        "language_hints": {"hidden": True},
                        "vad_model": {"hidden": True},
                        "vocabulary_id": {"hidden": True},
                        "vocabulary": {"hidden": True},
                    },
                },
            ],
        )
        self.assertEqual(
            [list(card.parameter_schema["properties"]) for card in cards[:2]],
            [[], ["keep_dialect", "speaker_diarization_enabled"]],
        )
        self.assertEqual(
            list(cards[2].parameter_schema["properties"]),
            [
                "keep_dialect",
                "semantic_punctuation_enabled",
            ],
        )


class _FakeDashScopeWebSocket:
    """Queue-backed provider socket for realtime protocol tests."""

    def __init__(self, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.sent: list[str | bytes] = []
        self.events: asyncio.Queue[str | None] = asyncio.Queue()
        self.close = AsyncMock(side_effect=self._close)

    def __aiter__(self) -> "_FakeDashScopeWebSocket":
        return self

    async def __anext__(self) -> str:
        event = await self.events.get()
        if event is None:
            raise StopAsyncIteration
        return event

    async def send(self, value: str | bytes) -> None:
        """Record outgoing frames and enqueue matching provider events."""
        self.sent.append(value)
        if isinstance(value, bytes):
            await self._put_event(
                "result-generated",
                {
                    "output": {
                        "sentence": {
                            "text": "hello",
                            "sentence_end": False,
                        },
                    },
                },
            )
        else:
            action = json.loads(value)["header"]["action"]
            if action == "run-task":
                if self.fail_start:
                    await self._put_event(
                        "task-failed",
                        header={
                            "error_code": "InvalidParameter",
                            "error_message": "bad input",
                        },
                    )
                else:
                    await self._put_event("task-started")
            elif action == "finish-task":
                await self._put_event(
                    "result-generated",
                    {
                        "output": {
                            "sentence": {
                                "text": "hello world",
                                "sentence_end": True,
                            },
                        },
                    },
                )
                await self._put_event("task-finished")
        await asyncio.sleep(0)

    async def _put_event(
        self,
        event: str,
        payload: dict | None = None,
        header: dict | None = None,
    ) -> None:
        await self.events.put(
            json.dumps(
                {
                    "header": {"event": event, **(header or {})},
                    "payload": payload or {},
                },
            ),
        )

    async def _close(self) -> None:
        await self.events.put(None)


class DashScopeRealtimeASRModelTest(IsolatedAsyncioTestCase):
    """Verify the native streaming protocol and failure mapping."""

    async def test_streams_pcm_and_returns_partial_and_final_text(
        self,
    ) -> None:
        """Map the complete realtime task lifecycle to ASR responses."""
        socket = _FakeDashScopeWebSocket()
        model = DashScopeRealtimeASRModel(
            credential=DashScopeCredential(api_key="test-key"),
            parameters=DashScopeRealtimeASRModel.Parameters(
                sample_rate=48000,
                language_hints=["en"],
            ),
        )

        with patch(
            "websockets.connect",
            new=AsyncMock(return_value=socket),
        ) as connect:
            async with model:
                partial = await model.push(b"pcm")
                final = await model.transcribe()

        connect.assert_awaited_once_with(
            "wss://dashscope.aliyuncs.com/api-ws/v1/inference",
            additional_headers={
                "Authorization": "Bearer test-key",
                "X-DashScope-DataInspection": "disable",
            },
        )
        run_task = json.loads(socket.sent[0])
        self.assertEqual(
            run_task["payload"],
            {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": "qwen-audio-3.1-asr-flash-streaming",
                "parameters": {
                    "sample_rate": 48000,
                    "language_hints": ["en"],
                    "keep_dialect": False,
                    "vad_model": "far_field_meeting_16k",
                    "semantic_punctuation_enabled": True,
                    "format": "pcm",
                },
                "input": {},
            },
        )
        self.assertEqual(socket.sent[1], b"pcm")
        self.assertEqual(partial.content.text, "hello")
        self.assertFalse(partial.is_last)
        self.assertEqual(final.content.text, "hello world")
        self.assertTrue(final.is_last)
        self.assertEqual(
            json.loads(socket.sent[2])["header"]["action"],
            "finish-task",
        )
        socket.close.assert_awaited_once()

    async def test_surfaces_task_start_failure(self) -> None:
        """Raise the provider's task-failed detail during connection."""
        socket = _FakeDashScopeWebSocket(fail_start=True)
        model = DashScopeRealtimeASRModel(
            credential=DashScopeCredential(api_key="test-key"),
        )

        with patch(
            "websockets.connect",
            new=AsyncMock(return_value=socket),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "InvalidParameter.*bad input",
            ):
                await model.connect()

        socket.close.assert_awaited_once()

    async def test_push_after_close_reports_not_connected(self) -> None:
        """Prefer the local connection state after an intentional close."""
        socket = _FakeDashScopeWebSocket()
        model = DashScopeRealtimeASRModel(
            credential=DashScopeCredential(api_key="test-key"),
        )

        with patch(
            "websockets.connect",
            new=AsyncMock(return_value=socket),
        ):
            await model.connect()
            await model.close()

        with self.assertRaisesRegex(RuntimeError, "not connected"):
            await model.push(b"pcm")
