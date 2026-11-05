# -*- coding: utf-8 -*-
"""DashScope batch and realtime ASR model tests."""

import asyncio
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from websockets.asyncio.server import ServerConnection, serve

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

    async def test_connect_sends_headers_with_real_client(self) -> None:
        """Exercise the installed client API against a local WebSocket."""
        received = {}

        async def handle_connection(websocket: ServerConnection) -> None:
            """Accept the recognition task and record handshake headers."""
            request = websocket.request
            event = json.loads(await websocket.recv())
            received.update(
                {
                    "path": request.path,
                    "authorization": request.headers["Authorization"],
                    "inspection": request.headers[
                        "X-DashScope-DataInspection"
                    ],
                    "action": event["header"]["action"],
                },
            )
            await websocket.send(
                json.dumps({"header": {"event": "task-started"}}),
            )
            await websocket.wait_closed()

        async with serve(handle_connection, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            model = DashScopeRealtimeASRModel(
                credential=DashScopeCredential(
                    api_key="test-key",
                    base_url=f"http://127.0.0.1:{port}",
                ),
            )
            async with asyncio.timeout(5):
                async with model:
                    self.assertEqual(
                        received,
                        {
                            "path": "/api-ws/v1/inference",
                            "authorization": "Bearer test-key",
                            "inspection": "disable",
                            "action": "run-task",
                        },
                    )

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
            "websockets.asyncio.client.connect",
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
            "websockets.asyncio.client.connect",
            new=AsyncMock(return_value=socket),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "InvalidParameter.*bad input",
            ):
                await model.connect()

        socket.close.assert_awaited_once()

    async def test_cleans_up_when_initial_send_fails(self) -> None:
        """Failed context entry releases resources and preserves the error."""
        # pylint: disable=protected-access
        # Each callback completes before the next subtest starts.
        # pylint: disable=cell-var-from-loop
        for error in (OSError("send failed"), TimeoutError("send timed out")):
            with self.subTest(error=type(error).__name__):
                socket = _FakeDashScopeWebSocket()
                model = DashScopeRealtimeASRModel(
                    credential=DashScopeCredential(api_key="test-key"),
                )
                reader = None

                async def fail_send(_: str) -> None:
                    """Capture the background task before failing the send."""
                    nonlocal reader
                    reader = model._reader
                    raise error

                self.addAsyncCleanup(model.close)
                with (
                    patch(
                        "websockets.asyncio.client.connect",
                        new=AsyncMock(return_value=socket),
                    ),
                    patch.object(socket, "send", side_effect=fail_send),
                ):
                    with self.assertRaises(type(error)) as raised:
                        async with model:
                            self.fail("Context entry must fail")

                self.assertIs(raised.exception, error)
                self.assertEqual(
                    {
                        "websocket": model._websocket,
                        "reader": model._reader,
                        "reader_done": reader.done(),
                        "close_calls": socket.close.await_count,
                    },
                    {
                        "websocket": None,
                        "reader": None,
                        "reader_done": True,
                        "close_calls": 1,
                    },
                )

    async def test_cleans_up_when_initialization_is_cancelled(self) -> None:
        """Cancellation during sending or startup still releases resources."""
        # pylint: disable=protected-access
        # Each callback completes before the next subtest starts.
        # pylint: disable=cell-var-from-loop
        for phase in ("send", "startup"):
            with self.subTest(phase=phase):
                socket = _FakeDashScopeWebSocket()
                model = DashScopeRealtimeASRModel(
                    credential=DashScopeCredential(api_key="test-key"),
                )
                reached = asyncio.Event()
                pending = asyncio.Event()

                async def send_without_response(_: str) -> None:
                    """Keep the provider silent until initialization ends."""
                    if phase == "send":
                        reached.set()
                        await pending.wait()

                async def wait_for_startup() -> None:
                    """Signal that connection setup is waiting for startup."""
                    reached.set()
                    await pending.wait()

                self.addAsyncCleanup(model.close)
                with (
                    patch(
                        "websockets.asyncio.client.connect",
                        new=AsyncMock(return_value=socket),
                    ),
                    patch.object(
                        socket,
                        "send",
                        side_effect=send_without_response,
                    ),
                    patch.object(
                        model._started,
                        "wait",
                        side_effect=wait_for_startup,
                    ),
                ):
                    entering = asyncio.create_task(model.connect())
                    try:
                        await asyncio.wait_for(reached.wait(), timeout=5)
                        reader = model._reader
                        entering.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await entering
                    finally:
                        entering.cancel()
                        await asyncio.gather(entering, return_exceptions=True)

                self.assertEqual(
                    {
                        "websocket": model._websocket,
                        "reader": model._reader,
                        "reader_done": reader.done(),
                        "close_calls": socket.close.await_count,
                        "cancelled": entering.cancelled(),
                    },
                    {
                        "websocket": None,
                        "reader": None,
                        "reader_done": True,
                        "close_calls": 1,
                        "cancelled": True,
                    },
                )

    async def test_cleans_up_when_startup_times_out(self) -> None:
        """Keep the startup timeout message while closing the connection."""
        socket = _FakeDashScopeWebSocket()
        model = DashScopeRealtimeASRModel(
            credential=DashScopeCredential(api_key="test-key"),
        )
        self.addAsyncCleanup(model.close)
        with (
            patch(
                "websockets.asyncio.client.connect",
                new=AsyncMock(return_value=socket),
            ),
            patch.object(socket, "send", new=AsyncMock()),
            patch(
                "agentscope.asr._dashscope._realtime_model."
                "_CONNECT_TIMEOUT_SECONDS",
                0,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Timed out waiting for DashScope ASR task startup",
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
            "websockets.asyncio.client.connect",
            new=AsyncMock(return_value=socket),
        ):
            await model.connect()
            await model.close()

        with self.assertRaisesRegex(RuntimeError, "not connected"):
            await model.push(b"pcm")


class DashScopeRealtimeSentenceJoinTest(TestCase):
    """Sentences must be joined the way the speaker said them."""

    def _model(self) -> DashScopeRealtimeASRModel:
        return DashScopeRealtimeASRModel(
            credential=DashScopeCredential(api_key="test-key"),
        )

    def _commit(self, model: DashScopeRealtimeASRModel, text: str) -> None:
        """Feed one finished sentence through the result handler."""
        model._handle_result(  # pylint: disable=protected-access
            {
                "payload": {
                    "output": {
                        "sentence": {"text": text, "sentence_end": True},
                    },
                },
            },
        )

    def test_cjk_sentences_are_not_separated_by_a_space(self) -> None:
        """``'你'.isalnum()`` is true, so the old word-space heuristic put
        a space between two Chinese sentences that had none."""
        model = self._model()
        self._commit(model, "你好")
        self._commit(model, "世界")

        self.assertEqual(
            model._complete_text(),  # pylint: disable=protected-access
            "你好世界",
        )

    def test_space_delimited_sentences_keep_a_word_separator(self) -> None:
        """Semantic punctuation ends the sentences, and English still needs
        the space the old ``isalnum`` check dropped after a period."""
        model = self._model()
        self._commit(model, "Hello.")
        self._commit(model, "World.")

        self.assertEqual(
            model._complete_text(),  # pylint: disable=protected-access
            "Hello. World.",
        )
