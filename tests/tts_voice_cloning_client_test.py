# -*- coding: utf-8 -*-
"""Voice creation SDK tests, independent of App and FastAPI."""
import inspect
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import httpx

from agentscope.credential import (
    CredentialBase,
    DashScopeCredential,
    OpenAICredential,
)
from agentscope.tts import (
    DashScopeVoiceCloningClient,
    OpenAIVoiceCloningClient,
    VoiceCloningError,
    VoiceCloningClientBase,
)


class TestVoiceCloningClients(IsolatedAsyncioTestCase):
    """Verify complete wire payloads, validation and provider failures."""

    def setUp(self) -> None:
        """Isolate network and environment settings for every test."""
        environment = patch.dict("os.environ", {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        transport = patch("agentscope.tts._voice_utils.httpx.AsyncClient")
        self.transport = transport.start()
        self.addCleanup(transport.stop)
        self.post = AsyncMock()
        self.transport.return_value.__aenter__.return_value.post = self.post
        self.openai = OpenAIVoiceCloningClient(
            OpenAICredential(api_key="test"),
        )
        self.dashscope = DashScopeVoiceCloningClient(
            DashScopeCredential(api_key="test"),
        )

    async def _create(self, operation: str) -> str:
        """Exercise a public SDK operation with a valid input."""
        if operation == "consent":
            return await self.openai.create_consent(
                b"\x00\x01\x02",
                name="Test consent",
                language="zh",
                filename="sample.mp3",
            )
        if operation == "openai":
            return await self.openai.create_voice(
                b"\x00\x01\x02",
                parameters=self.openai.Parameters(
                    name="Test voice",
                    consent="consent-1",
                ),
                filename="sample.mp3",
            )
        return await self.dashscope.create_voice(
            parameters=self.dashscope.Parameters(
                target_model="cosyvoice-v3-plus",
            ),
            audio_url="https://example.com/sample.mp3",
        )

    def test_common_client_contract_and_discovery(self) -> None:
        """Providers expose the same method and are found via credentials."""
        self.assertEqual(
            {
                cls.__name__: cls.get_voice_cloning_client_class()
                for cls in (
                    CredentialBase,
                    OpenAICredential,
                    DashScopeCredential,
                )
            },
            {
                "CredentialBase": None,
                "OpenAICredential": OpenAIVoiceCloningClient,
                "DashScopeCredential": DashScopeVoiceCloningClient,
            },
        )
        for client in (self.openai, self.dashscope):
            with self.subTest(client=type(client)):
                self.assertIsInstance(client, VoiceCloningClientBase)
                self.assertEqual(
                    inspect.signature(type(client).create_voice),
                    inspect.signature(VoiceCloningClientBase.create_voice),
                )
        with self.assertRaises(TypeError):
            # pylint: disable-next=abstract-class-instantiated
            VoiceCloningClientBase(CredentialBase())

    async def test_parameters_reject_missing_or_unknown_options(self) -> None:
        """Required and misspelled provider options cannot pass silently."""
        for client, valid in (
            (self.openai, {"consent": "cons-1"}),
            (self.dashscope, {"target_model": "qwen-audio-3.1-tts-flash"}),
        ):
            with self.subTest(client=type(client)):
                with self.assertRaises(ValueError):
                    await client.create_voice(b"sample")
                with self.assertRaises(ValueError):
                    client.Parameters(**valid, unsupported_option=True)
        self.transport.assert_not_called()

    async def test_model_audio_source_mismatch_is_rejected(self) -> None:
        """Input types cannot silently select a different model protocol."""
        cases = (
            ("cosyvoice-v3-plus", {"audio": b"sample"}),
            (
                "qwen-audio-3.1-tts-flash",
                {"audio_url": "https://example.com/sample.mp3"},
            ),
            (
                "qwen3-tts-vc-realtime-2025-11-27",
                {"audio_url": "https://example.com/sample.mp3"},
            ),
            ("unknown-model", {"audio": b"sample"}),
        )
        for model, inputs in cases:
            with self.subTest(model=model):
                with self.assertRaises(ValueError):
                    await self.dashscope.create_voice(
                        **inputs,
                        parameters=self.dashscope.Parameters(
                            target_model=model,
                        ),
                    )
        for inputs in (
            {"audio_url": "https://example.com/sample.mp3"},
            {
                "audio": b"sample",
                "audio_url": "https://example.com/sample.mp3",
            },
        ):
            with self.subTest(inputs=inputs):
                with self.assertRaises(ValueError):
                    await self.openai.create_voice(
                        **inputs,
                        parameters=self.openai.Parameters(consent="cons-1"),
                    )
        self.transport.assert_not_called()

    async def test_openai_uploads(self) -> None:
        """Both uploads honor credentials, environment and multipart fields."""
        cases = (
            ({}, {}, "https://api.openai.com/v1", {}),
            (
                {
                    "base_url": "https://proxy.example.test/v1/",
                    "organization": "org-credential",
                },
                {
                    "OPENAI_BASE_URL": "https://ignored.example.test/v1",
                    "OPENAI_ORG_ID": "org-environment",
                },
                "https://proxy.example.test/v1",
                {"OpenAI-Organization": "org-credential"},
            ),
            (
                {},
                {
                    "OPENAI_BASE_URL": "https://environment.example.test/v1/",
                    "OPENAI_ORG_ID": "org-environment",
                    "OPENAI_PROJECT_ID": "proj-environment",
                },
                "https://environment.example.test/v1",
                {
                    "OpenAI-Organization": "org-environment",
                    "OpenAI-Project": "proj-environment",
                },
            ),
        )
        for settings, environment, base_url, headers in cases:
            for operation in ("consent", "openai"):
                for status in (200, 201):
                    with self.subTest(
                        settings=settings,
                        operation=operation,
                        status=status,
                    ), patch.dict("os.environ", environment, clear=True):
                        self.openai = OpenAIVoiceCloningClient(
                            OpenAICredential(api_key="test", **settings),
                        )
                        self.post.reset_mock()
                        self.post.return_value = httpx.Response(
                            status,
                            json={"id": "created-id"},
                        )
                        self.assertEqual(
                            await self._create(operation),
                            "created-id",
                        )
                        is_consent = operation == "consent"
                        endpoint = "voice_consents" if is_consent else "voices"
                        field = "recording" if is_consent else "audio_sample"
                        self.post.assert_awaited_once_with(
                            f"{base_url}/audio/{endpoint}",
                            headers={
                                "Authorization": "Bearer test",
                                **headers,
                            },
                            data={"name": "Test consent", "language": "zh"}
                            if is_consent
                            else {
                                "name": "Test voice",
                                "consent": "consent-1",
                            },
                            files={
                                field: (
                                    "sample.mp3",
                                    b"\x00\x01\x02",
                                    "audio/mpeg",
                                ),
                            },
                        )

    async def test_dashscope_protocols(self) -> None:
        """Preserve Qwen Audio, Qwen voice enrollment and CosyVoice formats."""
        cases: list[tuple[str, dict[str, Any], str, dict[str, Any]]] = [
            (
                f"qwen-audio-{version}-tts-{tier}",
                {"audio": b"\x00\x01\x02"},
                "voice-enrollment",
                {
                    "action": "create_voice",
                    "prefix": "testvoice",
                    "audio": "data:audio/mpeg;base64,AAEC",
                },
            )
            for version in ("3.0", "3.1")
            for tier in ("flash", "plus")
        ]
        cases.extend(
            [
                (
                    "qwen3-tts-vc-realtime-2025-11-27",
                    {"audio": b"\x00\x01\x02"},
                    "qwen-voice-enrollment",
                    {
                        "action": "create",
                        "preferred_name": "testvoice",
                        "audio": {"data": "data:audio/mpeg;base64,AAEC"},
                        "text": "Transcript",
                    },
                ),
                (
                    "cosyvoice-v3-plus",
                    {"audio_url": "https://example.com/sample.mp3"},
                    "voice-enrollment",
                    {
                        "action": "create_voice",
                        "prefix": "testvoice",
                        "url": "https://example.com/sample.mp3",
                    },
                ),
            ],
        )
        for model, audio, enrollment, inputs in cases:
            for key in ("voice", "voice_id", "voice_name"):
                with self.subTest(model=model, response_key=key):
                    self.post.reset_mock()
                    self.post.return_value = httpx.Response(
                        200,
                        json={"output": {key: "voice-created"}},
                    )
                    self.assertEqual(
                        await self.dashscope.create_voice(
                            parameters=self.dashscope.Parameters(
                                target_model=model,
                                prefix="testvoice",
                                text="Transcript",
                            ),
                            filename="sample.mp3",
                            **audio,
                        ),
                        "voice-created",
                    )
                    self.assertEqual(
                        self.dashscope.get_enrollment_model(model),
                        enrollment,
                    )
                    self.post.assert_awaited_once_with(
                        "https://dashscope.aliyuncs.com/api/v1/services"
                        "/audio/tts/customization",
                        headers={
                            "Authorization": "Bearer test",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": enrollment,
                            "input": {"target_model": model, **inputs},
                        },
                    )

    async def test_dashscope_uses_credential_base_url(self) -> None:
        """Enrollment follows the credential host and proxy path prefix."""
        origins = (
            "https://dashscope.aliyuncs.com",
            "https://dashscope-intl.aliyuncs.com",
            "https://workspace.cn-beijing.maas.aliyuncs.com",
            "https://proxy.example.test:8443/gateway/dashscope",
            "http://localhost:8080/dashscope",
        )
        for origin in origins:
            for suffix in ("", "/compatible-mode/v1", "/api/v1"):
                for trailing in ("", "/"):
                    base_url = origin + suffix + trailing
                    with self.subTest(base_url=base_url):
                        self.post.reset_mock()
                        self.post.return_value = httpx.Response(
                            200,
                            json={"output": {"voice_id": "voice-created"}},
                        )
                        self.dashscope = DashScopeVoiceCloningClient(
                            DashScopeCredential(
                                api_key="test",
                                base_url=base_url,
                            ),
                        )
                        self.assertEqual(
                            await self._create("dashscope"),
                            "voice-created",
                        )
                        self.post.assert_awaited_once_with(
                            f"{origin}/api/v1/services"
                            "/audio/tts/customization",
                            headers={
                                "Authorization": "Bearer test",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": "voice-enrollment",
                                "input": {
                                    "action": "create_voice",
                                    "target_model": "cosyvoice-v3-plus",
                                    "prefix": "agentscope",
                                    "url": "https://example.com/sample.mp3",
                                },
                            },
                        )

    async def test_invalid_dashscope_base_url_fails_before_http(self) -> None:
        """Reject relative URLs and unsupported schemes before any request."""
        for base_url in (
            "",
            "/api/v1",
            "https:///api/v1",
            "ftp://host/api/v1",
        ):
            with self.subTest(base_url=base_url):
                self.dashscope = DashScopeVoiceCloningClient(
                    DashScopeCredential(api_key="test", base_url=base_url),
                )
                with self.assertRaises(ValueError):
                    await self._create("dashscope")
        self.transport.assert_not_called()

    async def test_invalid_inputs_do_not_send_requests(self) -> None:
        """Missing metadata or ambiguous audio fails before networking."""
        for operation in ("consent", "openai", "dashscope"):
            method = {
                "consent": self.openai.create_consent,
                "openai": self.openai.create_voice,
                "dashscope": self.dashscope.create_voice,
            }[operation]
            base: dict[str, Any] = {
                "audio": b"sample",
                "filename": "sample.mp3",
            }
            invalid: tuple[dict[str, Any], ...]
            if operation == "dashscope":
                base.update(target_model="qwen-audio-3.1-tts-flash")
                invalid = (
                    {"audio": None},
                    {"audio": b""},
                    {"filename": ""},
                    {"audio_url": "https://example.com/sample.mp3"},
                    {"audio": None, "audio_url": ""},
                    {"target_model": ""},
                    {"prefix": ""},
                )
            else:
                base.update(name="Test voice")
                required = "language" if operation == "consent" else "consent"
                base[required] = "en" if operation == "consent" else "cons-1"
                invalid = (
                    {"audio": b""},
                    {"filename": ""},
                    {"name": ""},
                    {required: ""},
                )
            for overrides in invalid:
                with self.subTest(operation=operation, overrides=overrides):
                    with self.assertRaises(ValueError):
                        values = {**base, **overrides}
                        if operation == "consent":
                            await method(**values)
                        else:
                            client = (
                                self.openai
                                if operation == "openai"
                                else self.dashscope
                            )
                            options = {
                                name: values.pop(name)
                                for name in client.Parameters.model_fields
                                if name in values
                            }
                            await method(
                                **values,
                                parameters=client.Parameters(**options),
                            )
        self.transport.assert_not_called()

    async def test_provider_failures_are_sdk_errors(self) -> None:
        """Expose SDK errors without retries or framework coupling."""
        for operation in ("consent", "openai", "dashscope"):
            provider = "DashScope" if operation == "dashscope" else "OpenAI"
            action = (
                "consent upload" if operation == "consent" else "voice clone"
            )
            for code in (400, 403, 429, 500):
                with self.subTest(operation=operation, code=code):
                    self.post.reset_mock()
                    self.post.return_value = httpx.Response(
                        code,
                        text="provider failure",
                    )
                    with self.assertRaises(VoiceCloningError) as ctx:
                        await self._create(operation)
                    self.assertEqual(
                        str(ctx.exception),
                        f"{provider} {action} failed ({code}): "
                        "provider failure",
                    )
                    self.post.assert_awaited_once()

    async def test_network_errors_are_sdk_errors(self) -> None:
        """Network failures retain their cause and do not retry creation."""
        for operation in ("consent", "openai", "dashscope"):
            for error in (httpx.ConnectError, httpx.ReadTimeout):
                with self.subTest(operation=operation, error=error):
                    self.post.reset_mock()
                    cause = error("offline")
                    self.post.side_effect = cause
                    with self.assertRaises(VoiceCloningError) as ctx:
                        await self._create(operation)
                    provider = (
                        "DashScope" if operation == "dashscope" else "OpenAI"
                    )
                    self.assertEqual(
                        str(ctx.exception),
                        f"Network error calling {provider}: offline",
                    )
                    self.assertIs(ctx.exception.__cause__, cause)
                    self.post.assert_awaited_once()

    async def test_dashscope_missing_voice_error_is_bounded(self) -> None:
        """A malformed success response cannot flood the API error detail."""
        result = {"output": {"unexpected": "a" * 2000}}
        self.post.return_value = httpx.Response(200, json=result)
        with self.assertRaises(VoiceCloningError) as ctx:
            await self._create("dashscope")
        self.assertEqual(
            str(ctx.exception),
            "DashScope returned success but no voice found in response: "
            + str(result)[:500],
        )

    async def test_invalid_success_payloads_are_sdk_errors(self) -> None:
        """Malformed JSON, unexpected structures and unusable IDs fail."""
        responses = [
            httpx.Response(200, content=body)
            for body in (b"<html>error</html>", b"", b"{", b"null", b"[]")
        ]
        responses.extend(
            httpx.Response(200, json=body)
            for body in (
                {},
                {"id": "", "output": {}},
                {"id": 1, "output": {"voice": 1}},
                {"id": [], "output": []},
                {"id": None, "output": None},
            )
        )
        for operation in ("consent", "openai", "dashscope"):
            for response in responses:
                with self.subTest(operation=operation, body=response.text):
                    self.post.return_value = response
                    with self.assertRaises(VoiceCloningError):
                        await self._create(operation)

    async def test_unknown_filename_uses_wav_mime_type(self) -> None:
        """Preserve the existing MIME fallback for unrecognized filenames."""
        self.post.return_value = httpx.Response(200, json={"id": "voice-1"})
        self.assertEqual(
            await self.openai.create_voice(
                b"sample",
                parameters=self.openai.Parameters(
                    name="Test voice",
                    consent="cons-1",
                ),
                filename="sample.unknown-audio-extension",
            ),
            "voice-1",
        )
        self.assertEqual(
            self.post.call_args.kwargs["files"],
            {
                "audio_sample": (
                    "sample.unknown-audio-extension",
                    b"sample",
                    "audio/wav",
                ),
            },
        )
