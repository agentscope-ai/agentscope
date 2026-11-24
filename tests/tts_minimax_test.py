# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the MiniMax TTS model."""
import asyncio
import base64
import json
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx

from agentscope.credential import CredentialFactory, MiniMaxCredential
from agentscope.tts import MiniMaxTTSModel, TTSResponse


_GLOBAL_BASE_URL = "https://api.minimax.io"
_CHINA_BASE_URL = "https://api.minimaxi.com"
_TTS_PATH = "/v1/t2a_v2"


class _StreamResponse:
    """Mock streaming response with newline-delimited payloads."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.headers = {"content-type": "text/event-stream"}

    @staticmethod
    def raise_for_status() -> None:
        """Simulate a successful HTTP response."""

    async def aiter_lines(self) -> Any:
        """Yield mock response lines."""
        for line in self.lines:
            yield line


class TestMiniMaxTTSModel(IsolatedAsyncioTestCase):
    """The unit tests for MiniMax TTS synthesis."""

    def setUp(self) -> None:
        """Replace the HTTP client with a mock."""
        self.client = MagicMock()
        self.client.__aenter__ = AsyncMock(return_value=self.client)
        self.client.__aexit__ = AsyncMock(return_value=None)
        self.client_patcher = patch(
            "httpx.AsyncClient",
            return_value=self.client,
        )
        self.client_patcher.start()

    def tearDown(self) -> None:
        """Restore the HTTP client constructor."""
        self.client_patcher.stop()

    @staticmethod
    def _make_model(
        stream: bool = False,
        **kwargs: Any,
    ) -> MiniMaxTTSModel:
        """Create a model with test credentials."""
        return MiniMaxTTSModel(
            credential=MiniMaxCredential(api_key="test"),
            stream=stream,
            **kwargs,
        )

    async def test_non_streaming_synthesis(self) -> None:
        """The non-streaming response contains the decoded audio."""
        audio = b"complete audio"
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {
            "data": {"audio": audio.hex(), "status": 2},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        }
        self.client.post = AsyncMock(return_value=response)
        model = self._make_model(stream=False)

        result = await model.synthesize(
            "Hello world",
            pronunciation_dict={"tone": ["world/wɜːrld"]},
            voice_modify={"pitch": 1},
            subtitle_enable=True,
        )

        self.assertIsInstance(result, TTSResponse)
        self.assertEqual(result.content.source.media_type, "audio/mpeg")
        self.assertEqual(
            base64.b64decode(result.content.source.data),
            audio,
        )
        self.client.post.assert_awaited_once()
        api_url, call_kwargs = self.client.post.call_args
        self.assertEqual(str(api_url[0]), _GLOBAL_BASE_URL + _TTS_PATH)
        payload = call_kwargs["json"]
        self.assertEqual(payload["model"], "speech-2.8-hd")
        self.assertEqual(payload["text"], "Hello world")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["output_format"], "hex")
        self.assertEqual(
            payload["voice_setting"]["voice_id"],
            "English_expressive_narrator",
        )
        self.assertEqual(payload["audio_setting"]["format"], "mp3")
        self.assertIn("pronunciation_dict", payload)
        self.assertIn("voice_modify", payload)
        self.assertTrue(payload["subtitle_enable"])

    async def test_regional_endpoint_is_credential_owned(self) -> None:
        """The endpoint remains credential-owned and supports chat URLs."""
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {
            "data": {"audio": b"wave".hex(), "status": 2},
            "base_resp": {"status_code": 0},
        }
        self.client.post = AsyncMock(return_value=response)
        for base, root in (
            (_CHINA_BASE_URL, _CHINA_BASE_URL),
            ("https://api.minimax.io/anthropic", _GLOBAL_BASE_URL),
            ("https://api.minimax.cn/anthropic/", "https://api.minimax.cn"),
            (
                "https://proxy.test/minimax/anthropic",
                "https://proxy.test/minimax",
            ),
        ):
            with self.subTest(base=base):
                credential = MiniMaxCredential(api_key="test", base_url=base)
                model = MiniMaxTTSModel(credential, stream=False)
                result = await model.synthesize("Hello")
                api_url, _ = self.client.post.call_args
                self.assertEqual(
                    (
                        str(api_url[0]),
                        credential.base_url,
                        result.content.source.media_type,
                    ),
                    (root + _TTS_PATH, base, "audio/mpeg"),
                )

    async def test_streaming_synthesis(self) -> None:
        """Streaming accepts SSE and plain JSON response lines."""
        first = b"first"
        second = b"second"
        lines = [
            "data: "
            + json.dumps(
                {
                    "data": {"audio": first.hex(), "status": 1},
                    "base_resp": {"status_code": 0},
                },
            ),
            "",
            json.dumps(
                {
                    "data": {
                        "audio": (first + second).hex(),
                        "status": 2,
                    },
                    "base_resp": {"status_code": 0},
                },
            ),
            "data: [DONE]",
        ]
        stream_response = _StreamResponse(lines)
        stream_context = MagicMock()
        stream_context.__aenter__ = AsyncMock(return_value=stream_response)
        stream_context.__aexit__ = AsyncMock(return_value=None)
        self.client.stream = Mock(return_value=stream_context)
        model = self._make_model(stream=True)

        generator = await model.synthesize(
            "Hello",
            audio_setting={"format": "wav", "sample_rate": 32000},
        )
        chunks = [chunk async for chunk in generator]

        self.assertEqual(
            [base64.b64decode(chunk.content.source.data) for chunk in chunks],
            [first, second],
        )
        self.assertEqual([chunk.is_last for chunk in chunks], [False, True])
        _, _, call_kwargs = self.client.stream.mock_calls[0]
        self.assertTrue(call_kwargs["json"]["stream"])
        self.assertEqual(call_kwargs["json"]["audio_setting"]["format"], "mp3")
        self.assertEqual(
            call_kwargs["json"]["audio_setting"]["sample_rate"],
            32000,
        )
        self.assertTrue(
            call_kwargs["json"]["stream_options"]["exclude_aggregated_audio"],
        )

    async def test_api_error(self) -> None:
        """An unsuccessful API status raises a clear error."""
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {
            "base_resp": {
                "status_code": 1001,
                "status_msg": "invalid parameter",
            },
        }
        self.client.post = AsyncMock(return_value=response)
        model = self._make_model(stream=False)

        with self.assertRaisesRegex(RuntimeError, "invalid parameter"):
            await model.synthesize("Hello")

    async def test_empty_input_short_circuits(self) -> None:
        """Empty input returns without sending an API request."""
        model = self._make_model(stream=False)
        self.client.post = AsyncMock()

        result = await model.synthesize("")

        self.assertIsNone(result.content)
        self.client.post.assert_not_awaited()

    async def test_model_cards_and_credential_wiring(self) -> None:
        """All supported models are discoverable from the credential."""
        expected_names = {
            "speech-2.8-hd",
            "speech-2.8-turbo",
            "speech-2.6-hd",
            "speech-2.6-turbo",
            "speech-02-hd",
            "speech-02-turbo",
            "speech-01-hd",
            "speech-01-turbo",
        }

        self.assertIs(
            CredentialFactory.get_credential_class("minimax_credential"),
            MiniMaxCredential,
        )
        self.assertEqual(
            {card.name for card in MiniMaxCredential.list_tts_models()},
            expected_names,
        )
        self.assertEqual(
            MiniMaxCredential.get_tts_model_classes(),
            [MiniMaxTTSModel],
        )
        self.assertTrue(MiniMaxCredential.list_models())
        self.assertNotIn("api_url", MiniMaxTTSModel.Parameters.model_fields)
        self.assertNotIn(
            "response_format",
            MiniMaxTTSModel.Parameters.model_fields,
        )


class _ByteStream(httpx.AsyncByteStream):
    """Fragment SSE bytes and optionally keep the connection open."""

    def __init__(self, content: str, wait: bool = False) -> None:
        self.content = content.encode()
        self.wait = wait
        self.waiting = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for offset in range(0, len(self.content), 7):
            yield self.content[offset : offset + 7]
        if self.wait:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self) -> None:
        """Record that the response released its underlying stream."""
        self.closed = True


def _event(audio: bytes = b"", status: int = 1) -> str:
    """Encode an SSE audio packet with an explicit provider status."""
    packet = {
        "data": {"audio": audio.hex(), "status": status},
        "base_resp": {"status_code": 0},
    }
    return "data: " + json.dumps(packet) + "\n\n"


class TestMiniMaxHTTPTransport(IsolatedAsyncioTestCase):
    """Exercise decoding and resource ownership using real HTTPX clients."""

    def setUp(self) -> None:
        """Replace only the network transport and track client lifetimes."""
        self.clients: list[httpx.AsyncClient] = []
        self.requests: list[httpx.Request] = []
        self.response = httpx.Response(
            200,
            json={
                "data": {"audio": b"audio".hex(), "status": 2},
                "base_resp": {"status_code": 0},
            },
        )
        client_cls = httpx.AsyncClient

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.response

        def create(**kwargs: Any) -> httpx.AsyncClient:
            client = client_cls(
                transport=httpx.MockTransport(handle),
                **kwargs,
            )
            self.clients.append(client)
            return client

        patcher = patch("httpx.AsyncClient", side_effect=create)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self) -> None:
        """Release clients even when a regression assertion fails."""
        for client in self.clients:
            await client.aclose()

    async def test_completion_without_audio_does_not_wait_for_eof(
        self,
    ) -> None:
        """An empty status=2 packet completes an otherwise open connection."""
        stream = _ByteStream(_event(b"first") + _event(status=2), wait=True)
        self.response = httpx.Response(200, stream=stream)
        model = MiniMaxTTSModel(MiniMaxCredential(api_key="test"))
        output = await model.synthesize("Hello")

        async def collect() -> list:
            return [chunk async for chunk in output]

        chunks = await asyncio.wait_for(collect(), timeout=0.5)
        self.assertEqual(
            [
                (base64.b64decode(c.content.source.data), c.is_last)
                for c in chunks
            ],
            [(b"first", True)],
        )
        self.assertTrue(stream.closed)
        self.assertTrue(all(client.is_closed for client in self.clients))

    async def test_incomplete_and_malformed_streams_raise(self) -> None:
        """Missing completion and invalid JSON cannot become success."""
        for wire in (
            _event(b"partial"),
            _event(b"partial") + "data: [DONE]\n\n",
            "data: {broken json}\n\n",
            "",
        ):
            with self.subTest(wire=wire):
                self.response = httpx.Response(
                    200,
                    stream=_ByteStream(wire),
                )
                model = MiniMaxTTSModel(MiniMaxCredential(api_key="test"))
                output = await model.synthesize("Hello")
                with self.assertRaises((RuntimeError, ValueError)):
                    async for _ in output:
                        pass
                self.assertTrue(self.response.is_closed)
                self.assertTrue(self.clients[-1].is_closed)

    async def test_mismatched_terminal_aggregate_is_rejected(self) -> None:
        """A mismatched aggregate cannot be appended as incremental audio."""
        self.response = httpx.Response(
            200,
            stream=_ByteStream(
                _event(b"aa") + _event(b"bb") + _event(b"XXaabb", 2),
            ),
        )
        model = MiniMaxTTSModel(MiniMaxCredential(api_key="test"))
        output = await model.synthesize("Hello")
        chunks = []
        with self.assertRaisesRegex(RuntimeError, "aggregate"):
            async for chunk in output:
                chunks.append(chunk)
        self.assertEqual(
            [
                (base64.b64decode(c.content.source.data), c.is_last)
                for c in chunks
            ],
            [(b"aa", False)],
        )
        self.assertTrue(self.clients[-1].is_closed)

    async def test_partial_settings_and_protected_request_fields(self) -> None:
        """Merge nested overrides while preserving the transport contract."""
        for stream in (False, True):
            with self.subTest(stream=stream):
                if stream:
                    self.response = httpx.Response(
                        200,
                        stream=_ByteStream(_event(b"a") + _event(status=2)),
                    )
                model = MiniMaxTTSModel(
                    MiniMaxCredential(api_key="test"),
                    stream=stream,
                    parameters=MiniMaxTTSModel.Parameters(
                        speed=1.5,
                        volume=2,
                        pitch=3,
                        sample_rate=24000,
                        bitrate=64000,
                        channel=2,
                    ),
                )
                voice = {"voice_id": "male-qn-qingse"}
                audio = {"sample_rate": 32000, "format": "wav"}
                output = await model.synthesize(
                    "Hello",
                    voice_setting=voice,
                    audio_setting=audio,
                    model="unexpected-model",
                    stream=not stream,
                    output_format="url",
                    stream_options={"exclude_aggregated_audio": False},
                    api_url="https://untrusted.test/tts",
                )
                if stream:
                    async for _ in output:
                        pass
                expected = {
                    "voice_setting": {
                        "voice_id": "male-qn-qingse",
                        "speed": 1.5,
                        "vol": 2,
                        "pitch": 3,
                    },
                    "audio_setting": {
                        "sample_rate": 32000,
                        "bitrate": 64000,
                        "format": "mp3",
                        "channel": 2,
                    },
                    "model": "speech-2.8-hd",
                    "text": "Hello",
                    "stream": stream,
                    "output_format": "hex",
                    "language_boost": "auto",
                    "stream_options": {"exclude_aggregated_audio": stream},
                    "api_url": "https://untrusted.test/tts",
                }
                request = self.requests[-1]
                self.assertEqual(json.loads(request.content), expected)
                self.assertEqual(
                    (str(request.url), request.headers["authorization"]),
                    ("https://api.minimax.io/v1/t2a_v2", "Bearer test"),
                )
                self.assertEqual(
                    (voice, audio),
                    (
                        {"voice_id": "male-qn-qingse"},
                        {"sample_rate": 32000, "format": "wav"},
                    ),
                )

    async def test_json_success_is_not_a_stream(self) -> None:
        """A nonstreaming success payload cannot masquerade as SSE."""
        model = MiniMaxTTSModel(MiniMaxCredential(api_key="test"))
        output = await model.synthesize("Hello")
        with self.assertRaisesRegex(RuntimeError, "instead of streamed"):
            async for _ in output:
                pass
        self.assertTrue(self.clients[-1].is_closed)

    async def test_nested_settings_require_objects(self) -> None:
        """Invalid nested overrides fail before creating an HTTP client."""
        model = MiniMaxTTSModel(MiniMaxCredential(api_key="test"))
        for name in ("voice_setting", "audio_setting"):
            with self.subTest(setting=name):
                with self.assertRaisesRegex(ValueError, name):
                    await model.synthesize("Hello", **{name: "invalid"})
        self.assertEqual((self.requests, self.clients), ([], []))

    async def test_nonstreaming_empty_audio(self) -> None:
        """An empty success has no audio block and releases its client."""
        self.response = httpx.Response(
            200,
            json={
                "data": None,
                "base_resp": {"status_code": 0},
            },
        )
        model = MiniMaxTTSModel(
            MiniMaxCredential(api_key="test"),
            stream=False,
        )
        result = await model.synthesize("Hello")
        self.assertEqual((result.content, result.is_last), (None, True))
        self.assertTrue(self.clients[-1].is_closed)

    async def test_json_and_sse_errors_propagate(self) -> None:
        """Decode whole JSON errors and SSE errors without losing status."""
        error = {
            "data": None,
            "base_resp": {"status_code": 1004, "status_msg": "auth failed"},
        }
        pretty = json.dumps(error, indent=2)
        for stream, content_type, body in (
            (False, "application/json", pretty),
            (True, "application/json; charset=utf-8", pretty),
            (
                True,
                "text/event-stream",
                "data: " + json.dumps(error) + "\n\n",
            ),
            (
                True,
                "text/event-stream",
                "\n".join("data: " + line for line in pretty.splitlines())
                + "\n\n",
            ),
        ):
            with self.subTest(stream=stream, content_type=content_type):
                self.response = httpx.Response(
                    200,
                    headers={"content-type": content_type},
                    text=body,
                )
                model = MiniMaxTTSModel(
                    MiniMaxCredential(api_key="test"),
                    stream=stream,
                )
                with self.assertRaisesRegex(RuntimeError, "1004"):
                    output = await model.synthesize("Hello")
                    if stream:
                        async for _ in output:
                            pass
                self.assertTrue(self.clients[-1].is_closed)

    async def test_client_cleanup_after_success_and_http_errors(self) -> None:
        """Every request owns its client, including ordinary synthesis."""
        for streaming in (False, True):
            for status in (200, 429):
                with self.subTest(streaming=streaming, status=status):
                    if streaming:
                        self.response = httpx.Response(
                            status,
                            stream=_ByteStream(
                                _event(b"first") + _event(status=2),
                            ),
                        )
                    else:
                        self.response = httpx.Response(
                            status,
                            json={
                                "data": {"audio": b"first".hex(), "status": 2},
                            },
                        )
                    model = MiniMaxTTSModel(
                        MiniMaxCredential(api_key="test"),
                        stream=streaming,
                    )

                    async def invoke(
                        current: MiniMaxTTSModel,
                        stream: bool,
                    ) -> None:
                        output = await current.synthesize("Hello")
                        if stream:
                            async for _ in output:
                                pass

                    if status == 200:
                        await invoke(model, streaming)
                    else:
                        with self.assertRaises(httpx.HTTPStatusError):
                            await invoke(model, streaming)
                    self.assertTrue(self.clients[-1].is_closed)

    async def test_early_close_and_cancellation_release_clients(self) -> None:
        """Consumers can stop or cancel synthesis without leaking clients."""
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                stream = _ByteStream(
                    _event(b"first") + _event(b"second"),
                    wait=True,
                )
                self.response = httpx.Response(200, stream=stream)
                model = MiniMaxTTSModel(MiniMaxCredential(api_key="test"))
                output = await model.synthesize("Hello")
                await anext(output)
                if cancel:
                    task = asyncio.create_task(anext(output))
                    await asyncio.wait_for(stream.waiting.wait(), timeout=0.5)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    await output.aclose()
                self.assertTrue(stream.closed)
                self.assertTrue(self.clients[-1].is_closed)
