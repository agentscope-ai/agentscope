# -*- coding: utf-8 -*-
"""ASR Web App route and model-construction tests."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, call, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agentscope.app._router import asr_router
from agentscope.app._service import get_asr_model
from agentscope.app.deps import (
    get_current_user_id,
    get_resource_access_service,
)
from agentscope.asr import ASRResponse, DashScopeASRModel
from agentscope.credential import DashScopeCredential, OpenAICredential
from agentscope.message import TextBlock


class ASRRouterTest(TestCase):
    """Exercise model discovery and multipart transcription."""

    def setUp(self) -> None:
        """Mount the ASR router with lightweight dependency overrides."""
        app = FastAPI()
        app.include_router(asr_router)
        self.access = object()
        app.dependency_overrides[get_current_user_id] = lambda: "alice"
        app.dependency_overrides[
            get_resource_access_service
        ] = lambda: self.access
        app.state.resource_access_service = self.access
        app.state.download_secret = "test-secret"
        self.client = self.enterContext(TestClient(app))

    def test_lists_batch_models(self) -> None:
        """Return every ASR card advertised by the provider."""
        response = self.client.get(
            "/asr-model/",
            params={"provider": "openai_credential"},
        )

        expected = [
            card.model_dump(mode="json")
            for card in OpenAICredential.list_asr_models()
        ]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"models": expected, "total": len(expected)},
        )

    def test_lists_dashscope_batch_and_realtime_models(self) -> None:
        """Expose both DashScope transcription modes in one selector."""
        response = self.client.get(
            "/asr-model/",
            params={"provider": "dashscope_credential"},
        )

        expected = [
            card.model_dump(mode="json")
            for card in DashScopeCredential.list_asr_models()
        ]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"models": expected, "total": len(expected)},
        )

    def test_unknown_provider_is_not_found(self) -> None:
        """Reject a provider discriminator that is not registered."""
        response = self.client.get(
            "/asr-model/",
            params={"provider": "missing_credential"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "Provider 'missing_credential' not found."},
        )

    def test_transcribes_multipart_audio(self) -> None:
        """Pass uploaded bytes and filename to the resolved ASR model."""
        model = SimpleNamespace(
            transcribe=AsyncMock(
                return_value=ASRResponse(
                    content=TextBlock(text="hello from audio"),
                ),
            ),
        )

        with patch(
            "agentscope.app._router._asr.get_asr_model",
            new=AsyncMock(return_value=model),
        ) as factory:
            response = self.client.post(
                "/asr/transcriptions",
                data={
                    "credential_id": "credential-1",
                    "model": "asr-1",
                    "parameters": '{"language":"en"}',
                },
                files={
                    "file": (
                        "speech.webm",
                        b"audio bytes",
                        "audio/webm",
                    ),
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"text": "hello from audio"})
        factory.assert_awaited_once_with(
            user_id="alice",
            credential_id="credential-1",
            model="asr-1",
            access=self.access,
            parameters={"language": "en"},
        )
        model.transcribe.assert_awaited_once_with(
            audio=b"audio bytes",
            filename="speech.webm",
        )

    def test_rejects_empty_audio(self) -> None:
        """Reject an empty recording before constructing an ASR model."""
        with patch(
            "agentscope.app._router._asr.get_asr_model",
            new=AsyncMock(),
        ) as factory:
            response = self.client.post(
                "/asr/transcriptions",
                data={"credential_id": "credential-1", "model": "asr-1"},
                files={"file": ("speech.webm", b"", "audio/webm")},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"detail": "Audio file must not be empty."},
        )
        factory.assert_not_awaited()

    def test_rejects_oversized_audio(self) -> None:
        """Reject a recording before reading beyond the service limit."""
        with (
            patch(
                "agentscope.app._router._asr._MAX_AUDIO_UPLOAD_BYTES",
                4,
            ),
            patch(
                "agentscope.app._router._asr.get_asr_model",
                new=AsyncMock(),
            ) as factory,
        ):
            response = self.client.post(
                "/asr/transcriptions",
                data={"credential_id": "credential-1", "model": "asr-1"},
                files={"file": ("speech.webm", b"audio", "audio/webm")},
            )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            response.json(),
            {"detail": "Audio file exceeds the 25 MiB upload limit."},
        )
        factory.assert_not_awaited()

    def test_rejects_invalid_model_parameters(self) -> None:
        """Map provider parameter validation failures to a bad request."""
        with self.assertRaises(ValueError) as context:
            DashScopeASRModel.Parameters(sample_rate=-5)

        with patch(
            "agentscope.app._router._asr.get_asr_model",
            new=AsyncMock(side_effect=context.exception),
        ):
            response = self.client.post(
                "/asr/transcriptions",
                data={
                    "credential_id": "credential-1",
                    "model": "asr-1",
                    "parameters": '{"sample_rate":-5}',
                },
                files={"file": ("speech.webm", b"audio", "audio/webm")},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("sample_rate", response.json()["detail"])

    def test_streams_realtime_audio(self) -> None:
        """Resolve a realtime model and proxy partial plus final text."""

        class _RealtimeModel:
            def __init__(self) -> None:
                self.audio = b""

            async def __aenter__(self) -> "_RealtimeModel":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def push(self, audio: bytes) -> ASRResponse:
                """Capture one browser audio frame."""
                self.audio = audio
                return ASRResponse(
                    content=TextBlock(text="partial"),
                    is_last=False,
                )

            async def transcribe(self) -> ASRResponse:
                """Return the final provider text."""
                return ASRResponse(
                    content=TextBlock(text="final text"),
                    is_last=True,
                )

        model = _RealtimeModel()
        with patch(
            "agentscope.app._router._asr.get_asr_model",
            new=AsyncMock(return_value=model),
        ) as factory:
            token_response = self.client.post(
                "/asr/realtime/token",
                json={
                    "credential_id": "credential-1",
                    "model": "streaming-asr",
                },
            )
            token = token_response.json()["token"]
            with self.client.websocket_connect(
                f"/asr/realtime?token={token}",
            ) as websocket:
                websocket.send_json(
                    {
                        "type": "start",
                        "credential_id": "credential-1",
                        "model": "streaming-asr",
                        "parameters": {"language_hints": ["en"]},
                        "sample_rate": 48000,
                    },
                )
                self.assertEqual(websocket.receive_json(), {"type": "ready"})
                websocket.send_bytes(b"pcm")
                self.assertEqual(
                    websocket.receive_json(),
                    {
                        "type": "transcript",
                        "text": "partial",
                        "is_last": False,
                    },
                )
                websocket.send_json({"type": "stop"})
                self.assertEqual(
                    websocket.receive_json(),
                    {
                        "type": "transcript",
                        "text": "final text",
                        "is_last": True,
                    },
                )

        self.assertEqual(model.audio, b"pcm")
        self.assertEqual(token_response.status_code, 200)
        self.assertIsInstance(token_response.json()["expires_at"], int)
        factory.assert_has_awaits(
            [
                call(
                    user_id="alice",
                    credential_id="credential-1",
                    model="streaming-asr",
                    access=self.access,
                    realtime=True,
                ),
                call(
                    user_id="alice",
                    credential_id="credential-1",
                    model="streaming-asr",
                    access=self.access,
                    parameters={
                        "language_hints": ["en"],
                        "sample_rate": 48000,
                    },
                    realtime=True,
                ),
            ],
        )

    def test_realtime_token_is_required_and_bound_to_config(self) -> None:
        """Reject missing tokens and tokens minted for another model."""
        start = {
            "type": "start",
            "credential_id": "credential-1",
            "model": "other-asr",
            "parameters": {},
            "sample_rate": 16000,
        }
        with patch(
            "agentscope.app._router._asr.get_asr_model",
            new=AsyncMock(return_value=object()),
        ) as factory:
            token_response = self.client.post(
                "/asr/realtime/token",
                json={
                    "credential_id": "credential-1",
                    "model": "streaming-asr",
                },
            )
            with self.client.websocket_connect(
                "/asr/realtime",
            ) as websocket:
                websocket.send_json(start)
                missing_token = websocket.receive_json()
            with self.client.websocket_connect(
                f"/asr/realtime?token={token_response.json()['token']}",
            ) as websocket:
                websocket.send_json(start)
                wrong_config = websocket.receive_json()

        self.assertEqual(
            missing_token,
            {"type": "error", "detail": "Realtime ASR token is required."},
        )
        self.assertEqual(
            wrong_config,
            {
                "type": "error",
                "detail": "Invalid or expired realtime ASR token.",
            },
        )
        factory.assert_awaited_once_with(
            user_id="alice",
            credential_id="credential-1",
            model="streaming-asr",
            access=self.access,
            realtime=True,
        )

    def test_rejects_invalid_realtime_start(self) -> None:
        """Close the realtime connection after an invalid start frame."""
        with self.client.websocket_connect("/asr/realtime") as websocket:
            websocket.send_json({"type": "start"})
            event = websocket.receive_json()

        self.assertEqual(event["type"], "error")
        self.assertIn("validation error", event["detail"])

    def test_rejects_invalid_parameters(self) -> None:
        """Reject non-object ASR parameters before model construction."""
        with patch(
            "agentscope.app._router._asr.get_asr_model",
            new=AsyncMock(),
        ) as factory:
            response = self.client.post(
                "/asr/transcriptions",
                data={
                    "credential_id": "credential-1",
                    "model": "asr-1",
                    "parameters": "[]",
                },
                files={"file": ("speech.webm", b"audio", "audio/webm")},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"detail": "ASR parameters must be a JSON object."},
        )
        factory.assert_not_awaited()


class ASRModelServiceTest(IsolatedAsyncioTestCase):
    """Resolve an exact ASR model against a visible credential."""

    async def test_builds_requested_model(self) -> None:
        """Use resource access and instantiate the advertising class."""

        class _Model:
            realtime = False

            @classmethod
            def list_models(cls) -> list:
                """Advertise one batch model for the service test."""
                return [SimpleNamespace(name="asr-1")]

            class Parameters:
                """Capture provider parameters for this service test."""

                def __init__(self, **values: object) -> None:
                    self.values = values

            def __init__(
                self,
                credential: object,
                model: str,
                parameters: object,
            ) -> None:
                self.credential = credential
                self.model = model
                self.parameters = parameters

        credential = SimpleNamespace(
            get_asr_model_classes=lambda: [_Model],
        )
        access = SimpleNamespace(
            resolve_credential=AsyncMock(
                return_value=SimpleNamespace(data={"type": "fake"}),
            ),
        )

        with patch(
            "agentscope.app._service._asr_model."
            "CredentialFactory.from_dict",
            return_value=credential,
        ):
            result = await get_asr_model(
                user_id="alice",
                credential_id="credential-1",
                model="asr-1",
                access=access,
                parameters={"language": "en"},
            )

        self.assertEqual(result.credential, credential)
        self.assertEqual(result.model, "asr-1")
        self.assertEqual(result.parameters.values, {"language": "en"})
        access.resolve_credential.assert_awaited_once_with(
            "alice",
            "credential-1",
        )

    async def test_builds_requested_realtime_model(self) -> None:
        """Select the realtime implementation only when requested."""

        class _RealtimeModel:
            realtime = True

            @classmethod
            def list_models(cls) -> list:
                """Advertise one realtime model for the service test."""
                return [SimpleNamespace(name="asr-stream")]

            class Parameters:
                """Capture realtime provider parameters."""

                def __init__(self, **values: object) -> None:
                    self.values = values

            def __init__(
                self,
                credential: object,
                model: str,
                parameters: object,
            ) -> None:
                self.credential = credential
                self.model = model
                self.parameters = parameters

        credential = SimpleNamespace(
            get_asr_model_classes=lambda: [_RealtimeModel],
        )
        access = SimpleNamespace(
            resolve_credential=AsyncMock(
                return_value=SimpleNamespace(data={"type": "fake"}),
            ),
        )

        with patch(
            "agentscope.app._service._asr_model."
            "CredentialFactory.from_dict",
            return_value=credential,
        ):
            result = await get_asr_model(
                user_id="alice",
                credential_id="credential-1",
                model="asr-stream",
                access=access,
                parameters={"sample_rate": 48000},
                realtime=True,
            )

        self.assertEqual(result.credential, credential)
        self.assertEqual(result.model, "asr-stream")
        self.assertEqual(result.parameters.values, {"sample_rate": 48000})

    async def test_rejects_unadvertised_model(self) -> None:
        """Do not silently substitute another model name."""

        class _Model:
            realtime = False

            @classmethod
            def list_models(cls) -> list:
                """Advertise a model different from the requested one."""
                return [SimpleNamespace(name="asr-1")]

        credential = SimpleNamespace(
            get_asr_model_classes=lambda: [_Model],
        )
        access = SimpleNamespace(
            resolve_credential=AsyncMock(
                return_value=SimpleNamespace(data={"type": "fake"}),
            ),
        )

        with patch(
            "agentscope.app._service._asr_model."
            "CredentialFactory.from_dict",
            return_value=credential,
        ):
            with self.assertRaisesRegex(
                HTTPException,
                "ASR model 'missing' is not available",
            ):
                await get_asr_model(
                    user_id="alice",
                    credential_id="credential-1",
                    model="missing",
                    access=access,
                )
