# -*- coding: utf-8 -*-
"""MiniMax voice creation protocol and App integration tests."""
import base64
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, call, patch
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from agentscope.app._router._schema import (
    CloneVoiceRequest,
    CreateVoiceProfileRequest,
)
from agentscope.app._router._schema._voice_profile import (
    MAX_MINIMAX_BASE64_LENGTH,
)
from agentscope.app._router._voice_profile import (
    _decode_audio,
    clone_voice,
    create_voice_profile,
    list_available_engines,
)
from agentscope.app._service._tts_model import get_tts_model
from agentscope.app.storage._model import (
    CredentialRecord,
    TTSModelConfig,
    VoiceProfileData,
)
from agentscope.credential import MiniMaxCredential
from agentscope.tts import (
    MiniMaxTTSModel,
    MiniMaxVoiceCloningClient,
    VoiceCloningError,
)
from agentscope.tts._minimax._voice_cloning_client import (
    MAX_CLONE_AUDIO_SIZE_BYTES,
)

_SUCCESS = {"base_resp": {"status_code": 0, "status_msg": "success"}}
_UPLOAD = {**_SUCCESS, "file": {"file_id": "123456789"}}
_VOICE_ID = "agentscope_00000000000000000000000000000001"


class TestMiniMaxVoiceCloning(IsolatedAsyncioTestCase):
    """Check complete provider requests and failures without live calls."""

    def setUp(self) -> None:
        """Isolate transport and generate deterministic voice IDs."""
        transport = patch("agentscope.tts._voice_utils.httpx.AsyncClient")
        self.transport = transport.start()
        self.addCleanup(transport.stop)
        self.post = AsyncMock()
        self.transport.return_value.__aenter__.return_value.post = self.post
        uuid = patch(
            "agentscope.tts._minimax._voice_cloning_client.uuid4",
            return_value=UUID(int=1),
        )
        uuid.start()
        self.addCleanup(uuid.stop)
        self.credential = MiniMaxCredential(api_key="test")
        self.client = MiniMaxVoiceCloningClient(self.credential)

    def _success(self) -> None:
        """Prepare successful upload and clone responses."""
        self.post.reset_mock()
        self.post.side_effect = [
            httpx.Response(200, json=_UPLOAD),
            httpx.Response(
                200,
                json={**_SUCCESS, "input_sensitive": {"type": 0}},
            ),
        ]

    async def test_content_check_accepts_normal_results(self) -> None:
        """Accept normal object checks and the documented boolean example."""
        cases: tuple[dict[str, Any], ...] = (
            {},
            {"input_sensitive": {"type": 0}},
            {"input_sensitive": False, "input_sensitive_type": 0},
        )
        for fields in cases:
            with self.subTest(fields=fields):
                self.post.side_effect = [
                    httpx.Response(200, json=_UPLOAD),
                    httpx.Response(200, json={**_SUCCESS, **fields}),
                ]
                self.assertEqual(
                    await self.client.create_voice(b"sample"),
                    _VOICE_ID,
                )

    async def test_complete_requests_across_hosts_and_formats(self) -> None:
        """Keep proxy prefixes and normalize filenames on every OS."""
        for base, root in (
            ("https://api.minimax.io/anthropic", "https://api.minimax.io"),
            ("https://api.minimax.cn/anthropic/", "https://api.minimax.cn"),
            ("https://api.minimaxi.com", "https://api.minimaxi.com"),
            (
                "https://proxy.test/minimax/anthropic",
                "https://proxy.test/minimax",
            ),
        ):
            for filename, name, mime in (
                ("/tmp/sample.mp3", "sample.mp3", "audio/mpeg"),
                (r"C:\samples\sample.M4A", "sample.M4A", "audio/mp4"),
                ("sample.wav", "sample.wav", "audio/wav"),
            ):
                with self.subTest(base=base, filename=filename):
                    self._success()
                    client = MiniMaxVoiceCloningClient(
                        MiniMaxCredential(api_key="test", base_url=base),
                    )
                    result = await client.create_voice(
                        b"sample",
                        filename=filename,
                    )
                    self.assertEqual(result, _VOICE_ID)
                    self.assertEqual(
                        self.post.await_args_list,
                        [
                            call(
                                root + "/v1/files/upload",
                                headers={"Authorization": "Bearer test"},
                                data={"purpose": "voice_clone"},
                                files={"file": (name, b"sample", mime)},
                            ),
                            call(
                                root + "/v1/voice_clone",
                                headers={"Authorization": "Bearer test"},
                                json={
                                    "file_id": 123456789,
                                    "voice_id": _VOICE_ID,
                                },
                            ),
                        ],
                    )

    async def test_invalid_inputs_never_upload(self) -> None:
        """Reject missing sources, unsupported formats and invalid IDs."""
        cases: tuple[dict[str, Any], ...] = (
            {},
            {"audio": b""},
            {"audio_url": "https://example.com/sample.wav"},
            {"audio": b"a", "audio_url": "https://example.com/sample.wav"},
            {"audio": b"a", "filename": "sample.ogg"},
            {"audio": b"a", "filename": ""},
            {"audio": b"a" * (MAX_CLONE_AUDIO_SIZE_BYTES + 1)},
        )
        for kwargs in cases:
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaises(ValueError):
                    await self.client.create_voice(**kwargs)
        for prefix in ("", "1voice", "音色", "has space", "a" * 224):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ValidationError):
                    self.client.Parameters(prefix=prefix)
        self.transport.assert_not_called()

    async def test_size_boundary_and_longest_prefix(self) -> None:
        """Accept the maximum sample and a valid 256-character voice ID."""
        self._success()
        result = await self.client.create_voice(
            b"a" * MAX_CLONE_AUDIO_SIZE_BYTES,
            parameters=self.client.Parameters(prefix="a" * 223),
        )
        self.assertEqual(result, "a" * 223 + "_" + UUID(int=1).hex)
        self.assertEqual(len(result), 256)

    async def test_business_and_transport_failures_stop_each_stage(
        self,
    ) -> None:
        """HTTP 200 is insufficient, and failed operations are not retried."""
        failures = [
            httpx.Response(200, json={}),
            httpx.Response(200, json={"base_resp": []}),
            httpx.Response(200, json={"base_resp": {}}),
            httpx.Response(
                200,
                json={"base_resp": {"status_code": 2038}},
            ),
            httpx.Response(
                200,
                json={**_SUCCESS, "input_sensitive": True},
            ),
            httpx.Response(200, json=[]),
            httpx.Response(200, text="invalid JSON"),
            httpx.Response(500, text="provider unavailable"),
            httpx.ReadTimeout("timeout"),
        ]
        failures.extend(
            httpx.Response(
                200,
                json={**_SUCCESS, "input_sensitive": {"type": code}},
            )
            for code in range(1, 8)
        )
        for stage in (0, 1):
            for failure in failures:
                with self.subTest(stage=stage, failure=repr(failure)):
                    self.post.reset_mock()
                    self.post.side_effect = [
                        httpx.Response(200, json=_UPLOAD),
                    ] * stage + [failure]
                    with self.assertRaises(VoiceCloningError):
                        await self.client.create_voice(b"sample")
                    self.assertEqual(self.post.await_count, stage + 1)

    async def test_invalid_upload_ids_never_clone(self) -> None:
        """Require a positive numeric file ID before requesting cloning."""
        for file in (
            None,
            [],
            {},
            *[
                {"file_id": value}
                for value in (None, "", "bad", 0, -1, True, 1.5, "１２")
            ],
        ):
            with self.subTest(file=file):
                self.post.reset_mock()
                self.post.side_effect = [
                    httpx.Response(200, json={**_SUCCESS, "file": file}),
                ]
                with self.assertRaises(VoiceCloningError):
                    await self.client.create_voice(b"sample")
                self.assertEqual(self.post.await_count, 1)

    async def test_clone_save_and_synthesize(self) -> None:
        """The App preserves the cloned ID and credential through TTS."""
        self._success()
        access, storage = AsyncMock(), AsyncMock()
        access.resolve_credential.return_value = CredentialRecord(
            id="credential-a",
            user_id="user-a",
            data={"type": "minimax_credential", "api_key": "test"},
        )
        audio = b"a" * (10 * 1024 * 1024 + 1)
        cloned = await clone_voice(
            CloneVoiceRequest(
                engine="minimax_tts",
                model="speech-2.8-hd",
                credential_id="credential-a",
                audio_base64=base64.b64encode(audio).decode("ascii"),
            ),
            "user-a",
            access,
        )
        self.assertEqual(
            cloned.model_dump(),
            {"voice_id": _VOICE_ID, "credential_id": "credential-a"},
        )
        self.assertEqual(
            self.post.await_args_list[0].kwargs["files"],
            {"file": ("voice.wav", audio, "audio/wav")},
        )
        data = VoiceProfileData(
            name="My voice",
            engine="minimax_tts",
            model="speech-2.8-hd",
            voice=cloned.voice_id,
            credential_id=cloned.credential_id,
        )
        storage.list_voice_profiles.return_value = []
        storage.upsert_voice_profile.return_value = "profile-a"
        saved = await create_voice_profile(
            CreateVoiceProfileRequest(data=data),
            "user-a",
            storage,
            access,
        )
        self.assertEqual(saved.model_dump(), {"profile_id": "profile-a"})
        record = storage.upsert_voice_profile.call_args.args[1]
        self.assertEqual(record.data.model_dump(), data.model_dump())
        storage.get_voice_profile.return_value = record
        model = await get_tts_model(
            "user-a",
            TTSModelConfig(
                type="minimax_credential",
                model="speech-2.8-hd",
                credential_id="credential-a",
                voice_profile_id="profile-a",
                parameters={},
            ),
            access,
            storage,
        )
        self.assertIsInstance(model, MiniMaxTTSModel)
        model.stream = False
        self.post.side_effect = [
            httpx.Response(
                200,
                json={**_SUCCESS, "data": {"audio": "6162", "status": 2}},
                request=httpx.Request("POST", "https://api.minimax.io"),
            ),
        ]
        result = await model.synthesize("Hello")
        self.assertEqual(result.content.source.data, "YWI=")
        self.assertEqual(
            self.post.call_args.kwargs["json"]["voice_setting"],
            {"voice_id": _VOICE_ID, "speed": 1.0, "vol": 1.0, "pitch": 0},
        )

    async def test_discovery_exposes_minimax_cloning(self) -> None:
        """Owned MiniMax credentials expose all supported speech models."""
        self.assertIs(
            MiniMaxCredential.get_voice_cloning_client_class(),
            MiniMaxVoiceCloningClient,
        )
        access = AsyncMock()
        access.list_resource.return_value = [
            CredentialRecord(
                id="credential-a",
                user_id="user-a",
                data={"type": "minimax_credential", "name": "MiniMax"},
            ),
        ]
        result = await list_available_engines("user-a", access)
        self.assertEqual(len(result.engine_details), 1)
        engine = result.engine_details[0]
        self.assertEqual(
            (engine.name, engine.credential_type, engine.voice_cloning),
            ("minimax_tts", "minimax_credential", True),
        )
        self.assertEqual(
            {card.name: card.voice_cloning for card in engine.models},
            {
                f"speech-{version}-{tier}": True
                for version in ("01", "02", "2.6", "2.8")
                for tier in ("hd", "turbo")
            },
        )

    async def test_route_maps_provider_error(self) -> None:
        """Surface provider failures as a gateway error, not a voice ID."""
        access = AsyncMock()
        access.resolve_credential.return_value = CredentialRecord(
            id="credential-a",
            user_id="user-a",
            data={"type": "minimax_credential", "api_key": "test"},
        )
        self.post.side_effect = [
            httpx.Response(200, json={"base_resp": {"status_code": 2038}}),
        ]
        with self.assertRaises(HTTPException) as raised:
            await clone_voice(
                CloneVoiceRequest(
                    engine="minimax_tts",
                    credential_id="credential-a",
                    audio_base64="AAEC",
                ),
                "user-a",
                access,
            )
        self.assertEqual(
            (raised.exception.status_code, raised.exception.detail),
            (502, "MiniMax API error 2038: unknown error"),
        )

    def test_app_limits_are_provider_specific(self) -> None:
        """Allow 20 MiB only for MiniMax and check decoded padding bytes."""
        audio = b"a" * MAX_CLONE_AUDIO_SIZE_BYTES
        encoded = base64.b64encode(audio).decode("ascii")
        body = CloneVoiceRequest(
            engine="minimax_tts",
            credential_id="credential-a",
            audio_base64=encoded,
        )
        self.assertEqual(
            _decode_audio(body.audio_base64, MAX_CLONE_AUDIO_SIZE_BYTES),
            audio,
        )
        for engine in ("openai_tts", "dashscope_tts", "cosyvoice"):
            with self.subTest(engine=engine):
                with self.assertRaises(ValidationError):
                    CloneVoiceRequest(
                        engine=engine,
                        credential_id="credential-a",
                        audio_base64=encoded,
                    )
        with self.assertRaises(ValidationError):
            CloneVoiceRequest(
                engine="minimax_tts",
                credential_id="credential-a",
                audio_base64="a" * (MAX_MINIMAX_BASE64_LENGTH + 1),
            )
        padded = base64.b64encode(audio + b"a").decode("ascii")
        self.assertEqual(len(padded), len(encoded))
        with self.assertRaises(HTTPException) as raised:
            _decode_audio(padded, MAX_CLONE_AUDIO_SIZE_BYTES)
        self.assertEqual(raised.exception.status_code, 413)
