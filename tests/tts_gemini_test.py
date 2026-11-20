# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the Gemini TTS module.

Covers:
  * ``GeminiTTSModel`` non-streaming synthesis from ``generateContent``
    responses with ``responseModalities: ["AUDIO"]``.
  * Handling of empty/missing text and empty audio responses.
"""
import asyncio
import base64
import io
import json
import os
import wave
import weakref
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

try:
    import aiohttp
except ImportError:
    aiohttp = None

try:
    from google import genai
except ImportError:
    genai = None

from agentscope.credential import GeminiCredential
from agentscope.tts import GeminiTTSModel, TTSResponse


_MEDIA_TYPE = "audio/wav"
# Gemini TTS emits 24kHz / mono / 16-bit PCM; the WAV wrapping in the model
# layer uses the same parameters.
_TTS_SAMPLE_RATE = 24000
_TTS_CHANNELS = 1
_TTS_SAMPLE_WIDTH = 2  # bytes (= 16 bit)
_NEW_MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts")


def _make_inline_data_part(
    data_bytes: bytes | None,
    mime_type: str | None = "audio/L16;codec=pcm;rate=24000",
) -> MagicMock:
    """Build a response part shaped like what the Gemini ``generateContent``
    API returns for ``responseModalities: ["AUDIO"]``."""
    part = MagicMock()
    if data_bytes is None:
        part.inline_data = None
        return part
    part.inline_data = MagicMock()
    part.inline_data.data = base64.b64encode(data_bytes).decode("ascii")
    part.inline_data.mime_type = mime_type
    return part


def _make_usage_metadata(
    prompt_token_count: int = 0,
    candidates_token_count: int = 0,
) -> MagicMock:
    """Build a usage metadata object shaped like what the Gemini API
    returns."""
    usage = MagicMock()
    usage.prompt_token_count = prompt_token_count
    usage.candidates_token_count = candidates_token_count
    return usage


def _make_api_response(
    chunks: list[bytes | None],
    usage: Any = None,
    mime_type: str | None = "audio/L16;codec=pcm;rate=24000",
) -> MagicMock:
    """Build a response like ``client.aio.models.generate_content``
    returns."""
    response = MagicMock()
    response.usage_metadata = usage
    candidate = MagicMock()
    candidate.content.parts = [
        _make_inline_data_part(c, mime_type) for c in chunks
    ]
    response.candidates = [candidate]
    return response


def _parse_wav_payload(wav_bytes: bytes) -> bytes:
    """Decode a full WAV file and return its raw PCM frames."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        return wav.readframes(wav.getnframes())


def _make_wav(pcm: bytes) -> bytes:
    """Build a real WAV fixture so duplicate headers are observable."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(_TTS_CHANNELS)
        wav.setsampwidth(_TTS_SAMPLE_WIDTH)
        wav.setframerate(_TTS_SAMPLE_RATE)
        wav.writeframes(pcm)
    return buf.getvalue()


class TestGeminiTTSModel(IsolatedAsyncioTestCase):
    """The unittests for the Gemini TTS model (non-realtime)."""

    def setUp(self) -> None:
        """Set up the test case."""
        try:
            import google.genai  # noqa: F401  pylint: disable=unused-import
        except ImportError:
            self.skipTest("google-genai is not installed")

        self.mock_client = MagicMock()
        self.mock_client.vertexai = False
        self.mock_client.__enter__.return_value = self.mock_client
        self.mock_client.aio.__aenter__.return_value = self.mock_client.aio
        self.mock_client.aio.models.generate_content = AsyncMock()
        self.patcher = patch(
            "google.genai.Client",
            return_value=self.mock_client,
        )
        self.patcher.start()

    def tearDown(self) -> None:
        """Tear down the test case."""
        self.patcher.stop()

    def _make_model(self) -> GeminiTTSModel:
        """Create a GeminiTTSModel with test credentials."""
        return GeminiTTSModel(
            credential=GeminiCredential(api_key="test"),
            model="gemini-2.5-flash-preview-tts",
            parameters=GeminiTTSModel.Parameters(voice="Kore"),
            stream=False,
        )

    async def test_synthesizes_audio(self) -> None:
        """The audio parts are concatenated into a self-contained WAV."""
        self.mock_client.aio.models.generate_content.return_value = (
            _make_api_response(
                [b"AAAA", b"BBBB", b"CCCC"],
                usage=_make_usage_metadata(
                    prompt_token_count=5,
                    candidates_token_count=10,
                ),
            )
        )
        model = self._make_model()

        result = await model.synthesize("Hello world")

        self.assertIsInstance(result, TTSResponse)
        self.assertEqual(result.content.source.media_type, _MEDIA_TYPE)
        wav_bytes = base64.b64decode(result.content.source.data)
        self.assertEqual(_parse_wav_payload(wav_bytes), b"AAAABBBBCCCC")
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            self.assertEqual(wav.getframerate(), _TTS_SAMPLE_RATE)
            self.assertEqual(wav.getnchannels(), _TTS_CHANNELS)
            self.assertEqual(wav.getsampwidth(), _TTS_SAMPLE_WIDTH)
        self.assertTrue(result.is_last)
        self.assertIsNotNone(result.usage)
        self.assertEqual(result.usage.input_tokens, 5)
        self.assertEqual(result.usage.output_tokens, 10)

    async def test_synthesizes_only_first_audio_candidate(self) -> None:
        """Alternative candidates must not be joined into one recording."""
        response = _make_api_response([b"AAAA"])
        response.candidates.extend(_make_api_response([b"BBBB"]).candidates)
        self.mock_client.aio.models.generate_content.return_value = response

        result = await self._make_model().synthesize("Hello world")

        wav_bytes = base64.b64decode(result.content.source.data)
        self.assertEqual(_parse_wav_payload(wav_bytes), b"AAAA")

    def test_rejects_multiple_wav_parts(self) -> None:
        """Reject separate WAV files instead of silently truncating audio."""
        response = _make_api_response(
            [_make_wav(b"AAAA"), None, _make_wav(b"BBBB")],
            mime_type="audio/wav",
        )
        with self.assertRaises(ValueError) as error:
            GeminiTTSModel._parse_response(response, 0)
        self.assertEqual(
            str(error.exception),
            "Gemini TTS returned multiple WAV audio parts.",
        )

    def test_single_wav_with_empty_parts_is_unchanged(self) -> None:
        """Preserve a single WAV even when other parts contain no audio."""
        wav_bytes = _make_wav(b"AAAA")
        response = _make_api_response(
            [None, wav_bytes, b"", None],
            mime_type="audio/wav",
        )
        result = GeminiTTSModel._parse_response(response, 0)
        self.assertEqual(
            (
                base64.b64decode(result.content.source.data),
                result.content.source.media_type,
            ),
            (wav_bytes, "audio/wav"),
        )

    async def test_stream_uses_only_first_audio_candidate(self) -> None:
        """Streaming must not append an alternative candidate's audio."""
        first_chunk = _make_api_response([b"AAAA"])
        first_chunk.candidates.extend(
            _make_api_response([b"BBBB"]).candidates,
        )
        second_chunk = _make_api_response([b"CCCC"])
        second_chunk.candidates.extend(
            _make_api_response([b"DDDD"]).candidates,
        )

        async def chunks() -> AsyncIterator[Any]:
            yield first_chunk
            yield second_chunk

        parser = GeminiTTSModel._parse_stream_into_async_generator(
            chunks(),
        )
        results = [item async for item in parser]

        self.assertEqual(len(results), 2)
        self.assertFalse(results[0].is_last)
        self.assertTrue(results[1].is_last)
        wav_bytes = b"".join(
            base64.b64decode(result.content.source.data) for result in results
        )
        self.assertEqual(_parse_wav_payload(wav_bytes), b"AAAACCCC")

    async def test_none_short_circuits(self) -> None:
        """``synthesize(None)`` returns an empty response without touching
        the API."""
        model = self._make_model()

        result = await model.synthesize(None)

        self.assertIsNone(result.content)
        self.mock_client.aio.models.generate_content.assert_not_called()

    async def test_empty_string_short_circuits(self) -> None:
        """``synthesize("")`` returns an empty response without touching
        the API."""
        model = self._make_model()

        result = await model.synthesize("")

        self.assertIsNone(result.content)
        self.mock_client.aio.models.generate_content.assert_not_called()

    async def test_empty_response_returns_empty_content(self) -> None:
        """A response with no audio parts yields an empty TTSResponse."""
        self.mock_client.aio.models.generate_content.return_value = (
            _make_api_response([None, None])
        )
        model = self._make_model()

        result = await model.synthesize("Hello world")

        self.assertIsNone(result.content)

    async def test_voice_config_passed_to_api(self) -> None:
        """The configured voice is forwarded to the API call's config."""
        self.mock_client.aio.models.generate_content.return_value = (
            _make_api_response([b"AAAA"])
        )
        model = GeminiTTSModel(
            credential=GeminiCredential(api_key="test"),
            model="gemini-2.5-pro-preview-tts",
            parameters=GeminiTTSModel.Parameters(voice="Puck"),
            stream=False,
        )

        await model.synthesize("Hello world")

        _, call_kwargs = self.mock_client.aio.models.generate_content.call_args
        config = call_kwargs["config"]
        self.assertEqual(config["response_modalities"], ["AUDIO"])
        voice_config = config["speech_config"]["voice_config"]
        self.assertEqual(
            voice_config["prebuilt_voice_config"]["voice_name"],
            "Puck",
        )
        self.assertEqual(call_kwargs["model"], "gemini-2.5-pro-preview-tts")

    def test_model_defaults_and_cards(self) -> None:
        """Default to streaming output and expose style only on new cards."""
        cards = {card.name: card for card in GeminiTTSModel.list_models()}
        names = (
            *_NEW_MODELS,
            "gemini-2.5-flash-preview-tts",
            "gemini-2.5-pro-preview-tts",
        )
        self.assertEqual(set(cards), set(names))
        for name in names:
            with self.subTest(model=name):
                model = GeminiTTSModel(
                    credential=GeminiCredential(api_key="test"),
                    model=name,
                )
                properties = cards[name].parameter_schema["properties"]
                self.assertEqual(
                    (model.stream, model.realtime, set(properties)),
                    (
                        True,
                        False,
                        {"voice", "style"}
                        if name in _NEW_MODELS
                        else {"voice"},
                    ),
                )
                self.assertEqual(
                    properties["voice"]["enum"],
                    cards[names[-1]].parameter_schema["properties"]["voice"][
                        "enum"
                    ],
                )

    async def test_api_version_overrides_and_vertex(self) -> None:
        """Respect explicit versions and leave Vertex defaults unchanged."""
        for vertexai, options, expected in (
            (False, {}, {"api_version": "v1beta"}),
            (False, {"api_version": "v1"}, None),
            (False, genai.types.HttpOptions(api_version="v1"), None),
            (True, {}, None),
        ):
            with self.subTest(vertexai=vertexai, options=options):
                self.mock_client.vertexai = vertexai
                self.mock_client.aio.models.generate_content.return_value = (
                    _make_api_response([b"AAAA"])
                )
                model = GeminiTTSModel(
                    credential=GeminiCredential(api_key="test"),
                    stream=False,
                    client_kwargs={"http_options": options},
                )
                await model.synthesize("Hello")
                call = self.mock_client.aio.models.generate_content.call_args
                self.assertEqual(
                    call.kwargs["config"].get("http_options"),
                    expected,
                )

    async def test_request_http_options_preserve_api_version(self) -> None:
        """Merge request options without mutating caller-owned values."""
        self.mock_client.aio.models.generate_content.return_value = (
            _make_api_response([b"AAAA"])
        )
        for vertexai, client_version, request_version, expected_version in (
            (False, None, None, "v1beta"),
            (False, None, "v1", "v1"),
            (False, "v1", None, None),
            (False, "v1", "v1beta", "v1beta"),
            (True, None, None, None),
        ):
            for typed in (False, True):
                with self.subTest(
                    vertexai=vertexai,
                    client_version=client_version,
                    request_version=request_version,
                    typed=typed,
                ):
                    original = {
                        "timeout": 1000,
                        "headers": {"x-test": "retained"},
                        "api_version": request_version,
                    }
                    options = (
                        genai.types.HttpOptions(**original)
                        if typed
                        else dict(original)
                    )
                    self.mock_client.vertexai = vertexai
                    model = GeminiTTSModel(
                        credential=GeminiCredential(api_key="test"),
                        stream=False,
                        client_kwargs={
                            "http_options": {"api_version": client_version},
                        },
                    )
                    await model.synthesize("Hello", http_options=options)
                    call = (
                        self.mock_client.aio.models.generate_content.call_args
                    )
                    actual = call.kwargs["config"]["http_options"]
                    if typed:
                        actual = actual.model_dump(exclude_unset=True)
                        options = options.model_dump(exclude_unset=True)
                    self.assertEqual(
                        (actual, options),
                        (
                            {**original, "api_version": expected_version},
                            original,
                        ),
                    )


class TestGeminiTTSSDKTransport(IsolatedAsyncioTestCase):
    """Verify request serialization and audio parsing through the real SDK."""

    def setUp(self) -> None:
        """Skip transport tests when the optional SDK is unavailable."""
        if genai is None:
            self.skipTest("google-genai is not installed")

    async def test_unstarted_stream_does_not_create_client(self) -> None:
        """Closing an unstarted stream must require no SDK cleanup."""
        model = GeminiTTSModel(
            credential=GeminiCredential(api_key="test"),
            model="gemini-3.8-flash-tts",
        )
        with patch("google.genai.Client") as create_client:
            stream = await model.synthesize("Hello")
            try:
                self.assertEqual(create_client.call_count, 0)
            finally:
                await stream.aclose()
            await asyncio.sleep(0)
            self.assertEqual(create_client.call_count, 0)

    async def test_aiohttp_connector_survives_stream_start(self) -> None:
        """Keep the SDK-owned aiohttp connector open across request awaits."""
        if aiohttp is None:
            self.skipTest("aiohttp is not installed")
        sessions = []

        async def request(
            session: Any,
            method: str,
            url: str,
            **_kwargs: Any,
        ) -> httpx.Response:
            """Exercise session lifetime without contacting Google."""
            sessions.append(session)
            await asyncio.sleep(0)
            self.assertEqual(
                (session.closed, session.connector is not None),
                (False, True),
            )
            self.assertEqual(
                (method, url),
                (
                    "post",
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    "gemini-3.8-flash-tts:streamGenerateContent?alt=sse",
                ),
            )
            return httpx.Response(
                200,
                text='data: {"usageMetadata":{"promptTokenCount":1}}\n\n',
            )

        model = GeminiTTSModel(
            credential=GeminiCredential(api_key="test"),
            model="gemini-3.8-flash-tts",
        )
        with patch.object(aiohttp.ClientSession, "_request", request):
            stream = await model.synthesize("Hello")
            responses = [chunk async for chunk in stream]
        self.assertEqual(
            (
                [
                    (r.content, r.is_last, r.usage.input_tokens)
                    for r in responses
                ],
                [session.closed for session in sessions],
            ),
            ([(None, True, 1)], [True]),
        )

    async def test_stream_owns_client_until_closed(self) -> None:
        """Keep SDK-owned clients alive, then close on every exit path."""
        for outcome in ("complete", "error", "early_close", "cancel"):
            with self.subTest(outcome=outcome):
                await self._check_client_lifetime(outcome)

    async def _check_client_lifetime(self, outcome: str) -> None:
        """Use owned transports so premature SDK cleanup is observable."""
        clients = []
        transports = []
        original_client = genai.Client

        def create_client(**kwargs: Any) -> Any:
            """Track the SDK client weakly without extending its lifetime."""
            client = original_client(**kwargs)
            clients.append(weakref.ref(client))
            transports.append(client._api_client._async_httpx_client)
            transports.append(client._api_client._httpx_client)
            return client

        async def handler(request: httpx.Request) -> httpx.Response:
            """Return PCM events or fail while consuming the SDK stream."""
            await asyncio.sleep(0)
            self.assertEqual(
                (
                    clients[0]() is not None,
                    [transport.is_closed for transport in transports],
                ),
                (True, [False, False]),
            )
            self.assertEqual(
                request.url.path,
                "/v1beta/models/gemini-3.8-flash-tts:streamGenerateContent",
            )
            if outcome == "error":
                raise RuntimeError("transport failed")
            if outcome == "cancel":
                raise asyncio.CancelledError()
            blob = {
                "mimeType": "audio/L16;rate=24000",
                "data": base64.b64encode(b"AAAA").decode("ascii"),
            }
            content = {"parts": [{"inlineData": blob}]}
            event = {"candidates": [{"content": content}]}
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"data: {json.dumps(event)}\n\n" * 3,
            )

        model = GeminiTTSModel(
            credential=GeminiCredential(api_key="test"),
            model="gemini-3.8-flash-tts",
            client_kwargs={
                "http_options": {
                    "async_client_args": {
                        "transport": httpx.MockTransport(handler),
                    },
                },
            },
        )
        with patch("google.genai.Client", side_effect=create_client):
            stream = await model.synthesize("Hello")
            try:
                await asyncio.sleep(0)
                self.assertEqual((clients, transports), ([], []))
                if outcome == "error":
                    with self.assertRaisesRegex(RuntimeError, "failed"):
                        await anext(stream)
                elif outcome == "cancel":
                    with self.assertRaises(asyncio.CancelledError):
                        await anext(stream)
                elif outcome == "early_close":
                    await anext(stream)
                    self.assertEqual(
                        [transport.is_closed for transport in transports],
                        [False, False],
                    )
                else:
                    responses = [chunk async for chunk in stream]
                    audio = b"".join(
                        base64.b64decode(chunk.content.source.data)
                        for chunk in responses
                    )
                    self.assertEqual(
                        (
                            _parse_wav_payload(audio),
                            [chunk.is_last for chunk in responses],
                            [t.is_closed for t in transports],
                        ),
                        (b"AAAA" * 3, [False, False, True], [True, True]),
                    )
            finally:
                await stream.aclose()
        self.assertEqual(
            [transport.is_closed for transport in transports],
            [True, True],
        )

    async def test_new_models_through_real_sdk(self) -> None:
        """Cover both models, unary WAV, and incremental PCM with usage."""
        for name in _NEW_MODELS:
            for streaming in (False, True):
                for api_version, env_version, request_version, expected in (
                    (None, "", None, "v1beta"),
                    (None, "v1", None, "v1"),
                    (None, "beta", None, "v1beta"),
                    (None, "v1beta", None, "v1beta"),
                    ("v1", "v1beta", None, "v1"),
                    ("v1beta", "v1", None, "v1beta"),
                    ("v1beta", "beta", "v1", "v1"),
                    ("v1", "v1", "v1beta", "v1beta"),
                ):
                    with self.subTest(
                        model=name,
                        streaming=streaming,
                        api_version=api_version,
                        env_version=env_version,
                        request_version=request_version,
                    ), patch.dict(
                        os.environ,
                        {"GOOGLE_GENAI_API_VERSION": env_version},
                    ):
                        await self._check_sdk_roundtrip(
                            name,
                            streaming,
                            api_version,
                            request_version,
                            expected,
                        )

    async def _check_sdk_roundtrip(
        self,
        name: str,
        streaming: bool,
        api_version: str | None,
        request_version: str | None,
        expected_version: str,
    ) -> None:
        """Keep each transport capture isolated from the parameter loop."""
        text, style = "你好 <sigh>", "warm and friendly"
        pcm = b"AAAA" + b"BBBB"
        wav_bytes = _make_wav(pcm)
        part = {"text": text, "speechMetadata": {"style": style}}
        expected_body = {
            "contents": [{"role": "user", "parts": [part]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voice_config": {"voice": "Kore"}},
            },
        }
        operation = "streamGenerateContent" if streaming else "generateContent"
        mime_type = "audio/L16;rate=24000" if streaming else "audio/wav"

        def audio_event(data: bytes) -> dict:
            """Build an audio response in the provider's wire format."""
            blob = {
                "mimeType": mime_type,
                "data": base64.b64encode(data).decode("ascii"),
            }
            content = {"parts": [{"inlineData": blob}]}
            return {"candidates": [{"content": content}]}

        def handler(request: httpx.Request) -> httpx.Response:
            """Check the entire request before returning unary or SSE audio."""
            self.assertEqual(
                (request.url.path, json.loads(request.content)),
                (
                    f"/{expected_version}/models/{name}:{operation}",
                    expected_body,
                ),
            )
            usage = {
                "usageMetadata": {
                    "promptTokenCount": 5,
                    "candidatesTokenCount": 10,
                },
            }
            if streaming:
                events = (audio_event(pcm[:4]), audio_event(pcm[4:]), usage)
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    text="".join(f"data: {json.dumps(e)}\n\n" for e in events),
                )
            return httpx.Response(
                200,
                json={**audio_event(wav_bytes), **usage},
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as http_client:
            http_options = {"httpx_async_client": http_client}
            if api_version is not None:
                http_options["api_version"] = api_version
            model = GeminiTTSModel(
                credential=GeminiCredential(api_key="test"),
                model=name,
                parameters=GeminiTTSModel.Parameters(
                    voice="Kore",
                    style=style,
                ),
                stream=streaming,
                client_kwargs={"http_options": http_options},
            )
            result = await model.synthesize(
                text,
                http_options={
                    "timeout": 1000,
                    "api_version": request_version,
                },
            )
            responses = (
                [chunk async for chunk in result] if streaming else [result]
            )
        payloads = [base64.b64decode(r.content.source.data) for r in responses]
        self.assertEqual(_parse_wav_payload(b"".join(payloads)), pcm)
        self.assertEqual(
            [(r.content.source.media_type, r.is_last) for r in responses],
            [("audio/wav", False), ("audio/wav", True)]
            if streaming
            else [("audio/wav", True)],
        )
        self.assertEqual(
            [r.usage for r in responses[:-1]],
            [None] if streaming else [],
        )
        usage = responses[-1].usage
        self.assertEqual(
            dict(usage),
            {
                "type": "tts",
                "input_tokens": 5,
                "output_tokens": 10,
                "time": usage.time,
            },
        )
        self.assertEqual(payloads[1:], [pcm[4:]] if streaming else [])
        if not streaming:
            self.assertEqual(payloads, [wav_bytes])
