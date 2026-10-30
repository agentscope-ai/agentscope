# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for Voice Profile storage model and constants.

Covers:
  * VoiceProfileData and VoiceProfileRecord model creation.
  * ENGINE_TO_CREDENTIAL_TYPE mapping completeness.
  * Credential-derived voice cloning capabilities.
"""
import base64
import typing
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from agentscope.app._router._voice_profile import (
    _decode_audio,
    _validate_voice_profile_data,
    clone_voice,
    list_available_engines,
    upload_openai_consent,
)
from agentscope.app._router._schema import (
    CloneVoiceRequest,
    OpenAIConsentRequest,
)
from agentscope.app._router._schema._voice_profile import (
    MAX_AUDIO_SIZE_BYTES,
    MAX_AUDIO_BASE64_LENGTH,
)
from agentscope.app._service._tts_model import (
    _validate_voice_binding,
    _config_error_detail,
    get_tts_model,
    validate_tts_model_config,
)
from agentscope.app.storage._model import (
    CredentialRecord,
    ENGINE_TO_CREDENTIAL_TYPE,
    TTSModelConfig,
    VoiceProfileData,
    VoiceProfileRecord,
)
from agentscope.app.storage._model._voice_profile import (
    _ENGINE_TYPE,
)
from agentscope.tts import (
    DashScopeCosyVoiceTTSModel,
    TTSModelCard,
    VoiceCloningError,
    VoiceCloningClientBase,
)
from agentscope.credential import (
    CredentialBase,
    CredentialFactory,
    DashScopeCredential,
    OpenAICredential,
)


class _DemoVoiceCloningClient(VoiceCloningClientBase):
    """A third-party client used to verify provider-independent dispatch."""

    class Parameters(VoiceCloningClientBase.Parameters):
        """Creation options understood by the demo provider."""

        name: str

    async def create_voice(
        self,
        audio: bytes | None = None,
        *,
        filename: str = "voice.wav",
        audio_url: str | None = None,
        parameters: BaseModel | None = None,
    ) -> str:
        """Return a provider ID without a network request."""
        return "demo-voice-id"


class _DemoVoiceCredential(CredentialBase):  # pylint: disable=abstract-method
    """A separately registered credential supplying its own client."""

    type: typing.Literal["demo_voice_credential"] = "demo_voice_credential"

    @classmethod
    def get_voice_cloning_client_class(
        cls,
    ) -> typing.Type[VoiceCloningClientBase]:
        """Expose the demo client's voice creation capability."""
        return _DemoVoiceCloningClient


class TestVoiceCloningClientDispatch(IsolatedAsyncioTestCase):
    """New providers use the common route without adding dispatch branches."""

    async def test_registered_provider_uses_common_route(self) -> None:
        """Credential registration and engine mapping select a third client."""
        access = AsyncMock()
        data = {
            "type": "demo_voice_credential",
            "id": "credential-demo",
            "name": "Demo credential",
        }
        access.resolve_credential.return_value = CredentialRecord(
            id="credential-demo",
            user_id="user-a",
            data=data,
        )
        with (
            patch.object(
                CredentialFactory,
                "_classes",
                list(CredentialFactory._classes),
            ),
            patch.object(CredentialFactory, "_adapter", None),
            patch.dict(
                ENGINE_TO_CREDENTIAL_TYPE,
                {"demo_engine": "demo_voice_credential"},
            ),
            patch.object(
                _DemoVoiceCloningClient,
                "create_voice",
                autospec=True,
                return_value="demo-voice-id",
            ) as create,
        ):
            CredentialFactory.register_credential(_DemoVoiceCredential)
            result = await clone_voice(
                CloneVoiceRequest(
                    engine="demo_engine",
                    credential_id="credential-demo",
                    audio_base64="AAEC",
                    audio_filename="sample.mp3",
                    prefix="Demo voice",
                ),
                "user-a",
                access,
            )
        self.assertEqual(
            result.model_dump(),
            {
                "voice_id": "demo-voice-id",
                "credential_id": "credential-demo",
            },
        )
        create.assert_awaited_once()
        client = create.call_args.args[0]
        self.assertEqual(client.credential.model_dump(), data)
        create.assert_awaited_once_with(
            client,
            b"\x00\x01\x02",
            filename="sample.mp3",
            audio_url=None,
            parameters=_DemoVoiceCloningClient.Parameters(name="Demo voice"),
        )

    async def test_unsupported_client_is_rejected(self) -> None:
        """Unknown engines and credentials without clients do not dispatch."""
        access = AsyncMock()
        for engine in ("unknown", "gemini_tts"):
            with self.subTest(engine=engine):
                with self.assertRaises(HTTPException) as ctx:
                    await clone_voice(
                        CloneVoiceRequest(
                            engine=engine,
                            credential_id="credential-a",
                        ),
                        "user-a",
                        access,
                    )
                self.assertEqual(ctx.exception.status_code, 400)
        access.resolve_credential.assert_not_called()

    async def test_common_route_keeps_credential_authorization(self) -> None:
        """Shared and wrong-provider credentials cannot reach any SDK call."""
        for engine, provider in (
            ("openai_tts", "openai_credential"),
            ("cosyvoice", "dashscope_credential"),
        ):
            for owner, actual_type, expected_status in (
                ("user-b", provider, 403),
                ("user-a", "gemini_credential", 400),
            ):
                with self.subTest(engine=engine, owner=owner):
                    access = AsyncMock()
                    access.resolve_credential.return_value = CredentialRecord(
                        id="credential-a",
                        user_id=owner,
                        data={"type": actual_type, "api_key": "test"},
                    )
                    with patch(
                        "agentscope.tts._voice_utils.httpx.AsyncClient",
                    ) as client:
                        with self.assertRaises(HTTPException) as ctx:
                            await clone_voice(
                                CloneVoiceRequest(
                                    engine=engine,
                                    credential_id="credential-a",
                                ),
                                "user-a",
                                access,
                            )
                    self.assertEqual(
                        ctx.exception.status_code,
                        expected_status,
                    )
                    client.assert_not_called()


class TestVoiceProfileModel(TestCase):
    """Unit tests for VoiceProfileData and VoiceProfileRecord."""

    def test_create_voice_profile_data_minimal(self) -> None:
        """VoiceProfileData can be created with only name."""
        data = VoiceProfileData(name="Test Voice")
        self.assertEqual(
            data.model_dump(),
            {
                "name": "Test Voice",
                "engine": None,
                "model": None,
                "credential_id": None,
                "voice": None,
                "metadata": None,
            },
        )

    def test_create_voice_profile_data_full(self) -> None:
        """VoiceProfileData can be created with all fields."""
        data = VoiceProfileData(
            name="Clone Voice",
            engine="dashscope_tts",
            model="qwen3-tts-flash",
            credential_id="cred-abc-123",
            voice="cosyvoice-clone-abc123",
            metadata={"quality": "high"},
        )
        self.assertEqual(
            data.model_dump(),
            {
                "name": "Clone Voice",
                "engine": "dashscope_tts",
                "model": "qwen3-tts-flash",
                "credential_id": "cred-abc-123",
                "voice": "cosyvoice-clone-abc123",
                "metadata": {"quality": "high"},
            },
        )

    def test_create_voice_profile_record(self) -> None:
        """VoiceProfileRecord can be created with auto id."""
        data = VoiceProfileData(name="Test")
        record = VoiceProfileRecord(
            user_id="user-1",
            data=data,
        )
        self.assertEqual(record.user_id, "user-1")
        self.assertEqual(record.data.name, "Test")
        self.assertIsNotNone(record.id)
        self.assertIsNotNone(record.created_at)

    def test_stored_source_does_not_affect_profile_binding(self) -> None:
        """Existing records remain readable after removing the source label."""
        expected = {
            "name": "Voice A",
            "engine": "dashscope_tts",
            "model": "qwen3-tts-flash",
            "credential_id": "credential-a",
            "voice": "voice-a",
            "metadata": {"label": "sample"},
        }
        for source in ("api", "local", None):
            with self.subTest(source=source):
                data = VoiceProfileData.model_validate(
                    {**expected, "source": source},
                )
                self.assertEqual(data.model_dump(), expected)


def _model_card(
    voices: list[str],
    *,
    voice_cloning: bool = False,
) -> TTSModelCard:
    """Build a minimal model card with provider-declared preset voices."""
    return TTSModelCard(
        name="gpt-4o-mini-tts",
        label="OpenAI TTS",
        voice_cloning=voice_cloning,
        parameter_schema={
            "type": "object",
            "properties": {
                "voice": {
                    "type": "string",
                    "enum": voices,
                },
            },
        },
        parameters_overrides={},
    )


def _custom_config(
    *,
    profile_id: str | None = "profile-a",
    credential_id: str = "credential-a",
    voice: str = "voice-a",
) -> TTSModelConfig:
    """Build a custom voice TTS config for authorization tests."""
    return TTSModelConfig(
        type="openai_credential",
        credential_id=credential_id,
        model="gpt-4o-mini-tts",
        voice_profile_id=profile_id,
        parameters={"voice": voice},
    )


def _profile(
    *,
    user_id: str = "user-a",
    credential_id: str = "credential-a",
    voice: str = "voice-a",
) -> VoiceProfileRecord:
    """Build an owner-scoped custom voice profile."""
    return VoiceProfileRecord(
        id="profile-a",
        user_id=user_id,
        data=VoiceProfileData(
            name="Voice A",
            engine="openai_tts",
            model="gpt-4o-mini-tts",
            credential_id=credential_id,
            voice=voice,
        ),
    )


class TestVoiceProfileTenantIsolation(IsolatedAsyncioTestCase):
    """Authorization tests for preset and custom voices."""

    async def test_omitted_voice_is_allowed_for_model_with_presets(
        self,
    ) -> None:
        """A normal preset-capable model may continue using its default."""
        storage = AsyncMock()
        config = _custom_config(profile_id=None)
        config.parameters = {}

        await _validate_voice_binding(
            user_id="user-a",
            config=config,
            credential_owner_id="user-b",
            storage=storage,
            card=_model_card(["alloy"], voice_cloning=True),
        )

        storage.get_voice_profile.assert_not_called()

    async def test_clone_only_model_requires_voice_profile(self) -> None:
        """A clone-only model cannot fall through to a class default voice."""
        storage = AsyncMock()
        config = _custom_config(profile_id=None)
        config.parameters = {}

        with self.assertRaises(HTTPException) as ctx:
            await _validate_voice_binding(
                user_id="user-a",
                config=config,
                credential_owner_id="user-a",
                storage=storage,
                card=_model_card([], voice_cloning=True),
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("requires a cloned voice profile", ctx.exception.detail)
        storage.get_voice_profile.assert_not_called()

    async def test_clone_only_card_cannot_use_shared_class_default(
        self,
    ) -> None:
        """Clone-only cards cannot fall back to the shared default voice."""
        card = next(
            card
            for card in DashScopeCosyVoiceTTSModel.list_models()
            if card.name == "cosyvoice-v3.5-plus"
        )
        config = TTSModelConfig(
            type="dashscope_credential",
            credential_id="credential-a",
            model=card.name,
            parameters={},
        )

        with self.assertRaises(HTTPException) as ctx:
            await _validate_voice_binding(
                user_id="user-a",
                config=config,
                credential_owner_id="user-a",
                storage=AsyncMock(),
                card=card,
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("requires a cloned voice profile", ctx.exception.detail)

    async def test_clone_only_model_rejects_unbound_explicit_voice(
        self,
    ) -> None:
        """Supplying a clone id directly cannot bypass profile ownership."""
        storage = AsyncMock()

        with self.assertRaises(HTTPException) as ctx:
            await _validate_voice_binding(
                user_id="user-a",
                config=_custom_config(profile_id=None, voice="voice-a"),
                credential_owner_id="user-b",
                storage=storage,
                card=_model_card([], voice_cloning=True),
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("requires a cloned voice profile", ctx.exception.detail)
        storage.get_voice_profile.assert_not_called()

    async def test_owned_keys_accept_unlisted_and_existing_cloned_voices(
        self,
    ) -> None:
        """Direct SDK/API voice IDs need no App profile for owned keys."""
        for card in (
            None,
            _model_card(["alloy"]),
            _model_card([], voice_cloning=True),
        ):
            with self.subTest(card=card):
                storage = AsyncMock()
                await _validate_voice_binding(
                    user_id="user-a",
                    config=_custom_config(profile_id=None, voice="voice-a"),
                    credential_owner_id="user-a",
                    storage=storage,
                    card=card,
                )
                storage.get_voice_profile.assert_not_called()
                with self.assertRaises(HTTPException):
                    await _validate_voice_binding(
                        user_id="user-a",
                        config=_custom_config(
                            profile_id=None,
                            voice="voice-a",
                        ),
                        credential_owner_id="user-b",
                        storage=storage,
                        card=card,
                    )

    async def test_preset_voice_does_not_require_profile(self) -> None:
        """Provider-declared preset voices remain usable as before."""
        storage = AsyncMock()
        await _validate_voice_binding(
            user_id="user-a",
            config=_custom_config(profile_id=None, voice="alloy"),
            credential_owner_id="user-b",
            storage=storage,
            card=_model_card(["alloy"]),
        )
        storage.get_voice_profile.assert_not_called()

    async def test_custom_voice_without_profile_is_rejected(self) -> None:
        """A caller cannot submit an arbitrary custom voice identifier."""
        with self.assertRaises(HTTPException) as ctx:
            await _validate_voice_binding(
                user_id="user-a",
                config=_custom_config(profile_id=None, voice="voice-b"),
                credential_owner_id="user-b",
                storage=AsyncMock(),
                card=_model_card(["alloy"]),
            )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_other_tenant_profile_is_hidden(self) -> None:
        """A profile id owned by user B is not resolvable for user A."""
        storage = AsyncMock()
        storage.get_voice_profile.return_value = None

        with self.assertRaises(HTTPException) as ctx:
            await _validate_voice_binding(
                user_id="user-a",
                config=_custom_config(profile_id="profile-b", voice="voice-b"),
                credential_owner_id="user-a",
                storage=storage,
                card=_model_card(["alloy"]),
            )

        self.assertEqual(ctx.exception.status_code, 404)
        storage.get_voice_profile.assert_awaited_once_with(
            "user-a",
            "profile-b",
        )

    async def test_shared_credential_is_rejected_for_custom_voice(
        self,
    ) -> None:
        """Sharing a credential does not implicitly share custom voices."""
        storage = AsyncMock()
        storage.get_voice_profile.return_value = _profile()

        with self.assertRaises(HTTPException) as ctx:
            await _validate_voice_binding(
                user_id="user-a",
                config=_custom_config(),
                credential_owner_id="user-b",
                storage=storage,
                card=_model_card(["alloy"]),
            )

        self.assertEqual(ctx.exception.status_code, 403)

    async def test_profile_binding_must_match_voice(self) -> None:
        """The voice id cannot be swapped after selecting an owned profile."""
        storage = AsyncMock()
        storage.get_voice_profile.return_value = _profile()

        with self.assertRaises(HTTPException) as ctx:
            await _validate_voice_binding(
                user_id="user-a",
                config=_custom_config(voice="voice-b"),
                credential_owner_id="user-a",
                storage=storage,
                card=_model_card(["alloy"]),
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("voice", ctx.exception.detail)

    async def test_exact_owned_profile_binding_is_allowed(self) -> None:
        """An owned profile with an exact binding passes validation."""
        storage = AsyncMock()
        storage.get_voice_profile.return_value = _profile()

        await _validate_voice_binding(
            user_id="user-a",
            config=_custom_config(),
            credential_owner_id="user-a",
            storage=storage,
            card=_model_card(["alloy"]),
        )

    async def test_write_and_runtime_reject_empty_binding_fields(self) -> None:
        """CRUD and runtime use the same non-empty binding-field rule."""
        access = AsyncMock()
        incomplete_profile = _profile(voice="")

        with self.assertRaises(HTTPException) as write_ctx:
            await _validate_voice_profile_data(
                access,
                "user-a",
                incomplete_profile.data,
            )

        self.assertEqual(write_ctx.exception.status_code, 400)
        self.assertIn("voice", write_ctx.exception.detail)
        access.resolve_credential.assert_not_called()

        storage = AsyncMock()
        storage.get_voice_profile.return_value = incomplete_profile
        with self.assertRaises(HTTPException) as runtime_ctx:
            await _validate_voice_binding(
                user_id="user-a",
                config=_custom_config(voice=""),
                credential_owner_id="user-a",
                storage=storage,
                card=_model_card(["alloy"]),
            )

        self.assertEqual(runtime_ctx.exception.status_code, 400)
        self.assertIn("incomplete", runtime_ctx.exception.detail)
        self.assertIn("voice", runtime_ctx.exception.detail)

    async def test_profile_cannot_bind_shared_credential(self) -> None:
        """Profile CRUD rejects a credential owned by another tenant."""
        access = AsyncMock()
        access.resolve_credential.return_value = CredentialRecord(
            id="credential-b",
            user_id="user-b",
            data={
                "type": "openai_credential",
                "api_key": "secret",
            },
        )
        data = _profile(credential_id="credential-b").data

        with self.assertRaises(HTTPException) as ctx:
            await _validate_voice_profile_data(
                access,
                "user-a",
                data,
            )

        self.assertEqual(ctx.exception.status_code, 403)

    async def test_full_config_validation_accepts_owned_binding(self) -> None:
        """Persisted-session validation resolves the same tenant binding."""
        access = AsyncMock()
        access.resolve_credential.return_value = CredentialRecord(
            id="credential-a",
            user_id="user-a",
            data={
                "type": "openai_credential",
                "api_key": "secret",
            },
        )
        storage = AsyncMock()
        storage.get_voice_profile.return_value = _profile()

        await validate_tts_model_config(
            "user-a",
            _custom_config(),
            access,
            storage,
        )

        access.resolve_credential.assert_awaited_once_with(
            "user-a",
            "credential-a",
        )
        storage.get_voice_profile.assert_awaited_once_with(
            "user-a",
            "profile-a",
        )


class TestVoiceProfileRuntime(IsolatedAsyncioTestCase):
    """Runtime follows current profiles with strict authorization checks."""

    def setUp(self) -> None:
        """Provide an owned credential and profile without cloud calls."""
        self.access = AsyncMock()
        self.access.resolve_credential.return_value = CredentialRecord(
            id="credential-a",
            user_id="user-a",
            data={"type": "openai_credential", "api_key": "test"},
        )
        self.storage = AsyncMock()
        self.storage.get_voice_profile.return_value = _profile()

    async def test_missing_profile_skips_tts_but_cannot_be_saved(self) -> None:
        """A deleted or invisible profile never synthesizes stale audio."""
        self.storage.get_voice_profile.return_value = None
        config = _custom_config()
        model = await get_tts_model(
            "user-a",
            config,
            self.access,
            self.storage,
        )
        self.assertIsNone(model)
        self.access.resolve_credential.assert_not_called()
        with self.assertRaises(HTTPException) as ctx:
            await validate_tts_model_config(
                "user-a",
                config,
                self.access,
                self.storage,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_invalid_credentials_skip_runtime_but_reject_saves(
        self,
    ) -> None:
        """Deleted, revoked and invalid credentials never reach synthesis."""
        for code in (400, 403, 404):
            with self.subTest(code=code):
                self.access.resolve_credential.side_effect = HTTPException(
                    code,
                )
                config = _custom_config()
                self.assertIsNone(
                    await get_tts_model(
                        "user-a",
                        config,
                        self.access,
                        self.storage,
                    ),
                )
                with self.assertRaises(HTTPException) as ctx:
                    await validate_tts_model_config(
                        "user-a",
                        config,
                        self.access,
                        self.storage,
                    )
                self.assertEqual(ctx.exception.status_code, code)

    async def test_invalid_parameters_skip_runtime_but_reject_saves(
        self,
    ) -> None:
        """Parameter validation is isolated at runtime and strict on save."""
        config = _custom_config()
        config.parameters["response_format"] = "invalid-format"
        self.assertIsNone(
            await get_tts_model(
                "user-a",
                config,
                self.access,
                self.storage,
            ),
        )
        with self.assertRaises(HTTPException) as ctx:
            await validate_tts_model_config(
                "user-a",
                config,
                self.access,
                self.storage,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_profile_model_is_validated_before_save(self) -> None:
        """Known incompatible clones fail; presets and new deployments work."""
        for model, voice, rejected in (
            ("tts-1", "custom-voice-id", True),
            ("tts-1-hd", "custom-voice-id", True),
            ("tts-1", "alloy", False),
            ("gpt-4o-mini-tts", "custom-voice-id", False),
            ("custom-deployment", "custom-voice-id", False),
        ):
            with self.subTest(model=model, voice=voice):
                data = _profile().data
                data.model = model
                data.voice = voice
                if rejected:
                    with self.assertRaises(HTTPException) as ctx:
                        await _validate_voice_profile_data(
                            self.access,
                            "user-a",
                            data,
                        )
                    self.assertEqual(ctx.exception.status_code, 400)
                    self.storage.get_voice_profile.return_value.data = data
                    self.assertIsNone(
                        await get_tts_model(
                            "user-a",
                            _custom_config(),
                            self.access,
                            self.storage,
                        ),
                    )
                else:
                    await _validate_voice_profile_data(
                        self.access,
                        "user-a",
                        data,
                    )

    async def test_owned_legacy_voices_work_without_profiles(self) -> None:
        """YAML voice removals and unknown models do not break owned keys."""
        for model, voice in (
            ("tts-1", "verse"),
            ("tts-1-hd", "ballad"),
            ("custom-deployment", "unlisted-voice"),
            ("gpt-4o-mini-tts", {"id": "custom-voice"}),
        ):
            with self.subTest(model=model, voice=voice):
                config = _custom_config(profile_id=None)
                config.model = model
                config.parameters = {"voice": voice}
                instance = await get_tts_model(
                    "user-a",
                    config,
                    self.access,
                    self.storage,
                )
                self.assertEqual(instance.parameters.voice, voice)
                await instance.client.close()
        self.storage.get_voice_profile.assert_not_called()

    async def test_stale_bindings_cannot_be_saved(self) -> None:
        """Runtime resolution does not weaken write-time binding checks."""
        for changes, mismatch in (
            ({"voice": "voice-new"}, "voice"),
            ({"model": "tts-1"}, "model"),
            ({"credential_id": "credential-new"}, "credential_id"),
            ({"engine": "cosyvoice"}, "provider type"),
        ):
            with self.subTest(changes=changes):
                profile = _profile()
                profile.data = profile.data.model_copy(update=changes)
                self.storage.get_voice_profile.return_value = profile
                config = _custom_config()
                before = config.model_dump()

                with self.assertRaises(HTTPException) as ctx:
                    await validate_tts_model_config(
                        "user-a",
                        config,
                        self.access,
                        self.storage,
                    )
                self.assertEqual(
                    (ctx.exception.status_code, ctx.exception.detail),
                    (
                        400,
                        "TTS config does not match voice profile "
                        f"'profile-a': {mismatch}.",
                    ),
                )
                self.assertEqual(config.model_dump(), before)

    async def test_runtime_uses_latest_voice_and_credential(self) -> None:
        """Recloning and credential edits override the session snapshot."""
        for voice, credential_id in (
            ("voice-new", "credential-a"),
            ("voice-a", "credential-new"),
            ("voice-new", "credential-new"),
        ):
            with self.subTest(voice=voice, credential_id=credential_id):
                self.storage.get_voice_profile.return_value = _profile(
                    voice=voice,
                    credential_id=credential_id,
                )
                self.access.resolve_credential.reset_mock()
                self.access.resolve_credential.return_value.id = credential_id
                config = _custom_config()
                config.parameters["response_format"] = "wav"
                before = config.model_dump()
                model = await get_tts_model(
                    "user-a",
                    config,
                    self.access,
                    self.storage,
                )
                self.assertIsNotNone(model)
                self.access.resolve_credential.assert_awaited_once_with(
                    "user-a",
                    credential_id,
                )
                self.assertEqual(
                    model.parameters.model_dump(),
                    {
                        "voice": {"id": voice},
                        "response_format": "wav",
                        "instructions": None,
                    },
                )
                self.assertEqual(config.model_dump(), before)
                await model.client.close()

    async def test_runtime_uses_latest_model_and_provider(self) -> None:
        """Model or provider edits select the current synthesis adapter."""
        profile = _profile(voice="voice-new", credential_id="credential-new")
        profile.data.engine = "dashscope_tts"
        profile.data.model = "qwen-audio-3.1-tts-flash"
        self.storage.get_voice_profile.return_value = profile
        self.access.resolve_credential.return_value = CredentialRecord(
            id="credential-new",
            user_id="user-a",
            data={"type": "dashscope_credential", "api_key": "test"},
        )
        for provider, old_model in (
            ("dashscope_credential", "qwen3-tts-vc-2026-01-22"),
            ("openai_credential", "gpt-4o-mini-tts"),
        ):
            with self.subTest(provider=provider):
                config = _custom_config()
                config.type = provider
                config.model = old_model
                config.parameters["realtime"] = True
                before = config.model_dump()
                self.access.resolve_credential.reset_mock()
                model = await get_tts_model(
                    "user-a",
                    config,
                    self.access,
                    self.storage,
                )
                self.assertIsInstance(model, DashScopeCosyVoiceTTSModel)
                self.assertEqual(
                    (model.model, model.parameters.model_dump()),
                    (
                        "qwen-audio-3.1-tts-flash",
                        {"voice": "voice-new", "realtime": False},
                    ),
                )
                self.access.resolve_credential.assert_awaited_once_with(
                    "user-a",
                    "credential-new",
                )
                self.assertEqual(config.model_dump(), before)

    async def test_incomplete_current_profile_skips_tts(self) -> None:
        """An incomplete profile cannot fall back to stale session values."""
        self.storage.get_voice_profile.return_value.data.voice = None
        self.assertIsNone(
            await get_tts_model(
                "user-a",
                _custom_config(),
                self.access,
                self.storage,
            ),
        )
        self.access.resolve_credential.assert_not_called()

    async def test_runtime_uses_one_owner_scoped_snapshot(self) -> None:
        """Runtime does not reread a profile after deriving its binding."""
        self.storage.get_voice_profile.side_effect = [
            _profile(),
            AssertionError("Unexpected second profile read"),
        ]
        model = await get_tts_model(
            "user-a",
            _custom_config(),
            self.access,
            self.storage,
        )
        self.assertIsNotNone(model)
        self.storage.get_voice_profile.assert_awaited_once_with(
            "user-a",
            "profile-a",
        )
        self.assertEqual(model.parameters.voice, {"id": "voice-a"})
        await model.client.close()

    async def test_validation_log_names_field_without_input(self) -> None:
        """Invalid parameter diagnostics identify fields but hide inputs."""
        config = _custom_config()
        config.parameters["response_format"] = "private-input"
        with patch("agentscope.app._service._tts_model.logger.warning") as log:
            self.assertIsNone(
                await get_tts_model(
                    "user-a",
                    config,
                    self.access,
                    self.storage,
                ),
            )
        self.assertEqual(
            log.call_args.args[-1],
            "response_format: literal_error",
        )

    async def test_short_parameters_leave_log_words_readable(self) -> None:
        """Ordinary voice, language and format values are not secrets."""
        config = _custom_config(profile_id=None)
        config.parameters = {
            "voice": "alloy",
            "language": "en",
            "response_format": "wav",
        }
        detail = "Credential endpoint cannot use alloy with en and wav."
        self.access.resolve_credential.side_effect = HTTPException(400, detail)
        with patch("agentscope.app._service._tts_model.logger.warning") as log:
            self.assertIsNone(
                await get_tts_model(
                    "user-a",
                    config,
                    self.access,
                    self.storage,
                ),
            )
        self.assertEqual(log.call_args.args[-1], f"HTTPException: {detail}")

    def test_credential_name_is_not_redacted(self) -> None:
        """Credential labels cannot replace ordinary error substrings."""
        credential = OpenAICredential(api_key="secret-key", name="en")
        self.assertEqual(
            _config_error_detail(ValueError("Invalid endpoint"), credential),
            "ValueError: Invalid endpoint",
        )

    def test_value_error_detail_is_redacted_and_bounded(self) -> None:
        """Constructor errors retain context without exposing known secrets."""
        credential = OpenAICredential(api_key="super-secret-key")
        error = ValueError(
            "Invalid endpoint https://host.test/?token=unknown-secret "
            "with super-secret-key and voice-a " + "x" * 600,
        )
        detail = _config_error_detail(error, credential)
        self.assertEqual(
            detail,
            (
                "ValueError: Invalid endpoint [URL] with [redacted] and "
                "voice-a " + "x" * 600
            )[:500],
        )

    async def test_profile_rename_keeps_tts_available(self) -> None:
        """Non-binding edits do not disable an otherwise valid voice."""
        self.storage.get_voice_profile.return_value.data.name = "Renamed"
        model = await get_tts_model(
            "user-a",
            _custom_config(),
            self.access,
            self.storage,
        )
        self.assertEqual(
            model.parameters.model_dump(),
            {
                "voice": {"id": "voice-a"},
                "response_format": "mp3",
                "instructions": None,
            },
        )
        await model.client.close()

    async def test_openai_voice_conversion_preserves_saved_config(
        self,
    ) -> None:
        """Only custom voices become objects after their binding is checked."""
        for voice, profile_id, expected in (
            ("voice_123", "profile-a", {"id": "voice_123"}),
            ("alloy", "profile-a", "alloy"),
            ("alloy", None, "alloy"),
            ("marin", "profile-a", "marin"),
            ("cedar", "profile-a", "cedar"),
            ("marin", None, "marin"),
            ("cedar", None, "cedar"),
        ):
            with self.subTest(voice=voice, profile_id=profile_id):
                config = _custom_config(voice=voice, profile_id=profile_id)
                before = config.model_dump()
                self.storage.get_voice_profile.return_value = _profile(
                    voice=voice,
                )
                model = await get_tts_model(
                    "user-a",
                    config,
                    self.access,
                    self.storage,
                )
                self.assertEqual(
                    model.parameters.model_dump(),
                    {
                        "voice": expected,
                        "response_format": "mp3",
                        "instructions": None,
                    },
                )
                self.assertEqual(config.model_dump(), before)
                await model.client.close()

    async def test_unsupported_openai_custom_voice_model(self) -> None:
        """Legacy models cannot be saved with a custom voice binding."""
        config = _custom_config()
        config.model = "tts-1"
        self.storage.get_voice_profile.return_value.data.model = "tts-1"
        with self.assertRaises(HTTPException) as ctx:
            await validate_tts_model_config(
                "user-a",
                config,
                self.access,
                self.storage,
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_malformed_audio_is_rejected_before_http(self) -> None:
        """Both upload routes reject invalid base64 without a provider call."""
        with patch(
            "agentscope.tts._voice_utils.httpx.AsyncClient",
        ) as client:
            for audio in ("a", "!!!!", "中文", ""):
                for operation in ("consent", "clone"):
                    with self.subTest(audio=audio, operation=operation):
                        with self.assertRaises(HTTPException) as ctx:
                            if operation == "consent":
                                await upload_openai_consent(
                                    OpenAIConsentRequest(
                                        credential_id="credential-a",
                                        audio_base64=audio,
                                    ),
                                    "user-a",
                                    self.access,
                                )
                            else:
                                await clone_voice(
                                    CloneVoiceRequest(
                                        engine="openai_tts",
                                        credential_id="credential-a",
                                        audio_base64=audio,
                                        consent="consent-1",
                                    ),
                                    "user-a",
                                    self.access,
                                )
                        self.assertEqual(ctx.exception.status_code, 400)
            client.assert_not_called()
        self.assertEqual(_decode_audio("AAEC"), b"\x00\x01\x02")

    async def test_clone_rejects_unsupported_synthesis_model(self) -> None:
        """An explicit OpenAI model must support custom speech."""
        with self.assertRaises(HTTPException) as ctx:
            await clone_voice(
                CloneVoiceRequest(
                    engine="openai_tts",
                    model="tts-1",
                    credential_id="credential-a",
                    audio_base64="AAEC",
                    consent="consent-1",
                ),
                "user-a",
                self.access,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.access.resolve_credential.assert_not_called()


class TestOpenAIVoiceRequestConfig(IsolatedAsyncioTestCase):
    """Consent and voice creation share the synthesis connection settings."""

    async def test_uploads_use_credential_settings(self) -> None:
        """Default, custom and environment settings reach both endpoints."""
        cases = (
            ({}, {}, "https://api.openai.com/v1", {}),
            (
                {
                    "base_url": "https://speech.example.test/proxy/v1/",
                    "organization": "org-credential",
                },
                {
                    "OPENAI_BASE_URL": "https://ignored.example.test/v1",
                    "OPENAI_ORG_ID": "org-environment",
                },
                "https://speech.example.test/proxy/v1",
                {"OpenAI-Organization": "org-credential"},
            ),
            (
                {"base_url": None, "organization": None},
                {
                    "OPENAI_BASE_URL": "https://environment.example.test/v1",
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
            for operation in ("consent", "clone"):
                with self.subTest(settings=settings, operation=operation):
                    access = AsyncMock()
                    access.resolve_credential.return_value = CredentialRecord(
                        id="credential-a",
                        user_id="user-a",
                        data={
                            "type": "openai_credential",
                            "api_key": "test",
                            **settings,
                        },
                    )
                    with (
                        patch.dict("os.environ", environment, clear=True),
                        patch(
                            "agentscope.tts._voice_utils.httpx.AsyncClient",
                        ) as client,
                    ):
                        post = AsyncMock(
                            return_value=httpx.Response(
                                201,
                                json={"id": "created-id"},
                            ),
                        )
                        client.return_value.__aenter__.return_value.post = post
                        if operation == "consent":
                            result = await upload_openai_consent(
                                OpenAIConsentRequest(
                                    credential_id="credential-a",
                                    audio_base64="AAEC",
                                    audio_filename="sample.mp3",
                                    language="zh",
                                    name="Test consent",
                                ),
                                "user-a",
                                access,
                            )
                            endpoint = "voice_consents"
                            data = {"name": "Test consent", "language": "zh"}
                            file_field = "recording"
                            expected = {"consent_id": "created-id"}
                        else:
                            result = await clone_voice(
                                CloneVoiceRequest(
                                    engine="openai_tts",
                                    model="gpt-4o-mini-tts",
                                    credential_id="credential-a",
                                    audio_base64="AAEC",
                                    audio_filename="sample.mp3",
                                    consent="consent-1",
                                    prefix="Test voice",
                                ),
                                "user-a",
                                access,
                            )
                            endpoint = "voices"
                            data = {
                                "name": "Test voice",
                                "consent": "consent-1",
                            }
                            file_field = "audio_sample"
                            expected = {
                                "voice_id": "created-id",
                                "credential_id": "credential-a",
                            }
                    post.assert_awaited_once_with(
                        f"{base_url}/audio/{endpoint}",
                        headers={"Authorization": "Bearer test", **headers},
                        data=data,
                        files={
                            file_field: (
                                "sample.mp3",
                                b"\x00\x01\x02",
                                "audio/mpeg",
                            ),
                        },
                    )
                    self.assertEqual(result.model_dump(), expected)


class TestDashScopeVoiceCloneProtocol(IsolatedAsyncioTestCase):
    """Cloning requests use the protocol required by the target model."""

    async def test_clone_request_payloads(self) -> None:
        """Qwen Audio uploads use voice-enrollment without changing peers."""
        cases: list[tuple[str, str, str, dict[str, object]]] = [
            (
                "dashscope_tts",
                f"qwen-audio-{version}-tts-{tier}",
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
                    "dashscope_tts",
                    "qwen3-tts-vc-2026-01-22",
                    "qwen-voice-enrollment",
                    {
                        "action": "create",
                        "preferred_name": "testvoice",
                        "audio": {"data": "data:audio/mpeg;base64,AAEC"},
                        "text": "Sample transcript",
                    },
                ),
                (
                    "cosyvoice",
                    "cosyvoice-v3-plus",
                    "voice-enrollment",
                    {
                        "action": "create_voice",
                        "prefix": "testvoice",
                        "url": "https://example.com/sample.mp3",
                    },
                ),
            ],
        )
        access = AsyncMock()
        access.resolve_credential.return_value = CredentialRecord(
            id="credential-a",
            user_id="user-a",
            data={
                "type": "dashscope_credential",
                "api_key": "test",
                "base_url": (
                    "https://workspace.cn-beijing.maas.aliyuncs.com"
                    "/compatible-mode/v1"
                ),
            },
        )
        for engine, model, enrollment, expected_input in cases:
            with self.subTest(model=model):
                with patch(
                    "agentscope.tts._voice_utils.httpx.AsyncClient",
                ) as client:
                    post = AsyncMock(
                        return_value=httpx.Response(
                            200,
                            json={"output": {"voice_id": "voice-1"}},
                        ),
                    )
                    client.return_value.__aenter__.return_value.post = post
                    result = await clone_voice(
                        CloneVoiceRequest(
                            engine=engine,
                            model=model,
                            credential_id="credential-a",
                            audio_base64="AAEC"
                            if engine != "cosyvoice"
                            else None,
                            audio_filename="sample.mp3",
                            audio_url="https://example.com/sample.mp3"
                            if engine == "cosyvoice"
                            else None,
                            text="Sample transcript",
                            prefix="testvoice",
                        ),
                        "user-a",
                        access,
                    )
                post.assert_awaited_once_with(
                    "https://workspace.cn-beijing.maas.aliyuncs.com"
                    "/api/v1/services/audio/tts/customization",
                    json={
                        "model": enrollment,
                        "input": {**expected_input, "target_model": model},
                    },
                    headers={
                        "Authorization": "Bearer test",
                        "Content-Type": "application/json",
                    },
                )
                self.assertEqual(
                    result.model_dump(),
                    {"voice_id": "voice-1", "credential_id": "credential-a"},
                )


class TestVoiceProfileUpstreamResponse(IsolatedAsyncioTestCase):
    """Success status codes still require a valid upstream JSON body."""

    async def _request(
        self,
        operation: str,
        response: httpx.Response,
    ) -> dict:
        """Call a voice endpoint with a mocked upstream response."""
        access = AsyncMock()
        access.resolve_credential.return_value = CredentialRecord(
            id="credential-a",
            user_id="user-a",
            data={
                "type": (
                    "dashscope_credential"
                    if operation == "dashscope"
                    else "openai_credential"
                ),
                "api_key": "test",
            },
        )
        with patch(
            "agentscope.tts._voice_utils.httpx.AsyncClient",
        ) as client:
            client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=response,
            )
            if operation == "consent":
                result = await upload_openai_consent(
                    OpenAIConsentRequest(
                        credential_id="credential-a",
                        audio_base64="AAEC",
                    ),
                    "user-a",
                    access,
                )
            else:
                result = await clone_voice(
                    CloneVoiceRequest(
                        engine=(
                            "cosyvoice"
                            if operation == "dashscope"
                            else "openai_tts"
                        ),
                        credential_id="credential-a",
                        model="cosyvoice-v3-plus"
                        if operation == "dashscope"
                        else None,
                        audio_url="https://example.com/sample.wav"
                        if operation == "dashscope"
                        else None,
                        audio_base64="AAEC"
                        if operation != "dashscope"
                        else None,
                        consent="consent-1",
                    ),
                    "user-a",
                    access,
                )
        return result.model_dump()

    async def test_non_json_success_returns_bad_gateway(self) -> None:
        """HTML, empty, and malformed success bodies all become HTTP 502."""
        for operation in ("consent", "openai", "dashscope"):
            statuses = (200,) if operation == "dashscope" else (200, 201)
            for code in statuses:
                for body in (b"<html>proxy error</html>", b"", b"{"):
                    with self.subTest(
                        operation=operation,
                        status=code,
                        body=body,
                    ):
                        with self.assertRaises(HTTPException) as ctx:
                            await self._request(
                                operation,
                                httpx.Response(code, content=body),
                            )
                        provider = (
                            "DashScope"
                            if operation == "dashscope"
                            else "OpenAI"
                        )
                        self.assertEqual(
                            (ctx.exception.status_code, ctx.exception.detail),
                            (
                                502,
                                f"{provider} returned an invalid JSON "
                                "response.",
                            ),
                        )

    async def test_sdk_errors_keep_http_boundary(self) -> None:
        """SDK input and provider errors become HTTP 400 and 502."""
        for operation, method in (
            ("consent", "OpenAIVoiceCloningClient.create_consent"),
            ("openai", "OpenAIVoiceCloningClient.create_voice"),
            ("dashscope", "DashScopeVoiceCloningClient.create_voice"),
        ):
            for error, status_code in (
                (ValueError("invalid audio"), 400),
                (VoiceCloningError("upstream failure"), 502),
            ):
                with self.subTest(operation=operation, status=status_code):
                    with patch(
                        f"agentscope.tts.{method}",
                        new_callable=AsyncMock,
                        side_effect=error,
                    ) as create:
                        with self.assertRaises(HTTPException) as ctx:
                            await self._request(
                                operation,
                                httpx.Response(200, json={}),
                            )
                    self.assertEqual(
                        (ctx.exception.status_code, ctx.exception.detail),
                        (status_code, str(error)),
                    )
                    self.assertIs(ctx.exception.__cause__, error)
                    create.assert_awaited_once()

    async def test_valid_success_preserves_response(self) -> None:
        """Provider ids are still returned in the public response schema."""
        for operation, payload, expected in (
            ("consent", {"id": "consent-1"}, {"consent_id": "consent-1"}),
            (
                "openai",
                {"id": "voice-1"},
                {"voice_id": "voice-1", "credential_id": "credential-a"},
            ),
            (
                "dashscope",
                {"output": {"voice_id": "voice-1"}},
                {"voice_id": "voice-1", "credential_id": "credential-a"},
            ),
        ):
            with self.subTest(operation=operation):
                self.assertEqual(
                    await self._request(
                        operation,
                        httpx.Response(200, json=payload),
                    ),
                    expected,
                )


class TestEngineToCredentialMapping(TestCase):
    """Unit tests for ENGINE_TO_CREDENTIAL_TYPE mapping."""

    def test_api_engines_map_correctly(self) -> None:
        """All engines map to their respective credentials."""
        expected = {
            "cosyvoice": "dashscope_credential",
            "dashscope_tts": "dashscope_credential",
            "openai_tts": "openai_credential",
            "gemini_tts": "gemini_credential",
        }
        self.assertEqual(ENGINE_TO_CREDENTIAL_TYPE, expected)

    def test_all_engines_have_mapping(self) -> None:
        """Every _ENGINE_TYPE value has a credential mapping."""
        args = typing.get_args(_ENGINE_TYPE)
        for engine in args:
            self.assertIn(
                engine,
                ENGINE_TO_CREDENTIAL_TYPE,
                f"Missing mapping for engine '{engine}'",
            )


class TestVoiceAudioLimits(TestCase):
    """Reject oversized uploads before provider requests or extra encoding."""

    def test_base64_and_decoded_size_limits(self) -> None:
        """Boundary-sized audio works; padding cannot hide an extra byte."""
        audio = b"a" * MAX_AUDIO_SIZE_BYTES
        encoded = base64.b64encode(audio).decode("ascii")
        for schema, extra in (
            (CloneVoiceRequest, {"engine": "openai_tts"}),
            (OpenAIConsentRequest, {}),
        ):
            with self.subTest(schema=schema):
                request = schema(
                    credential_id="credential-a",
                    audio_base64=encoded,
                    **extra,
                )
                self.assertEqual(_decode_audio(request.audio_base64), audio)
                with self.assertRaises(ValidationError) as ctx:
                    schema(
                        credential_id="credential-a",
                        audio_base64="a" * (MAX_AUDIO_BASE64_LENGTH + 1),
                        **extra,
                    )
                self.assertEqual(
                    [
                        (error["loc"], error["type"])
                        for error in ctx.exception.errors()
                    ],
                    [(("audio_base64",), "string_too_long")],
                )
        oversized = base64.b64encode(audio + b"a").decode("ascii")
        self.assertLessEqual(len(oversized), MAX_AUDIO_BASE64_LENGTH)
        with self.assertRaises(HTTPException) as ctx:
            _decode_audio(oversized)
        self.assertEqual(ctx.exception.status_code, 413)


class TestEngineCapabilities(IsolatedAsyncioTestCase):
    """Engine discovery follows the credential's SDK capability."""

    async def test_cloning_flag_tracks_credential_client(self) -> None:
        """Changing a credential client changes engine info immediately."""
        access = AsyncMock()
        access.list_resource.return_value = [
            CredentialRecord(
                id="credential-a",
                user_id="user-a",
                data={"type": "openai_credential", "name": "OpenAI"},
            ),
        ]
        card = _model_card(["alloy"], voice_cloning=True).model_copy(
            update={"name": "custom-deployment"},
        )
        for supported in (False, True):
            with self.subTest(supported=supported):
                with (
                    patch.object(
                        OpenAICredential,
                        "get_voice_cloning_client_class",
                        return_value=_DemoVoiceCloningClient
                        if supported
                        else None,
                    ),
                    patch.object(
                        OpenAICredential,
                        "list_tts_models",
                        return_value=[card],
                    ),
                ):
                    response = await list_available_engines("user-a", access)
                self.assertEqual(
                    response.model_dump(),
                    {
                        "engine_details": [
                            {
                                "name": "openai_tts",
                                "credential_type": "openai_credential",
                                "models": [
                                    {
                                        **card.model_dump(),
                                        "enrollment_model": None,
                                    },
                                ],
                                "voice_cloning": supported,
                                "credentials": [
                                    {"id": "credential-a", "label": "OpenAI"},
                                ],
                            },
                        ],
                    },
                )

    async def test_dashscope_models_and_enrollment_metadata(self) -> None:
        """Expose SDK models with existing App grouping and owned keys only."""
        access = AsyncMock()
        access.list_resource.return_value = [
            CredentialRecord(
                id="shared",
                user_id="other",
                data={"type": "dashscope_credential", "name": "Shared"},
            ),
            CredentialRecord(
                id="owned",
                user_id="user-a",
                data={"type": "dashscope_credential", "name": "DashScope"},
            ),
        ]
        cases = (
            ("qwen3-tts-flash", True, "qwen-voice-enrollment"),
            ("qwen-audio-3.1-tts-flash", True, "voice-enrollment"),
            ("cosyvoice-v3-plus", True, "voice-enrollment"),
            ("qwen-preset-only", False, None),
        )
        cards = [
            _model_card([], voice_cloning=cloning).model_copy(
                update={"name": name},
            )
            for name, cloning, _ in cases
        ]
        with patch.object(
            DashScopeCredential,
            "list_tts_models",
            return_value=cards,
        ) as load_models:
            response = await list_available_engines("user-a", access)
            load_models.assert_called_once_with()
        expected_models = [
            {**card.model_dump(), "enrollment_model": enrollment}
            for card, (_, _, enrollment) in zip(cards, cases)
        ]
        common = {
            "credential_type": "dashscope_credential",
            "voice_cloning": True,
            "credentials": [{"id": "owned", "label": "DashScope"}],
        }
        self.assertEqual(
            response.model_dump(),
            {
                "engine_details": [
                    {
                        **common,
                        "name": "cosyvoice",
                        "models": [expected_models[2]],
                    },
                    {
                        **common,
                        "name": "dashscope_tts",
                        "models": [
                            expected_models[0],
                            expected_models[1],
                            expected_models[3],
                        ],
                    },
                ],
            },
        )

        # A later request reloads cards, including an empty updated registry.
        with patch.object(
            DashScopeCredential,
            "list_tts_models",
            return_value=[],
        ) as load_models:
            refreshed = await list_available_engines("user-a", access)
            load_models.assert_called_once_with()
        self.assertEqual(
            refreshed.model_dump(),
            {
                "engine_details": [
                    {**common, "name": "cosyvoice", "models": []},
                    {**common, "name": "dashscope_tts", "models": []},
                ],
            },
        )

        access.list_resource.return_value = access.list_resource.return_value[
            :1
        ]
        response = await list_available_engines("user-a", access)
        self.assertEqual(response.model_dump(), {"engine_details": []})
