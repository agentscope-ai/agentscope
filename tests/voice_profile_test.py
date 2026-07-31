# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for Voice Profile storage model and constants.

Covers:
  * VoiceProfileData and VoiceProfileRecord model creation.
  * ENGINE_TO_CREDENTIAL_TYPE mapping completeness.
  * ENGINE_VOICE_CLONING mapping.
"""
import typing
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from fastapi import HTTPException

from agentscope.app._router._voice_profile import (
    _validate_voice_profile_data,
)
from agentscope.app._service._tts_model import (
    _validate_voice_binding,
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
    ENGINE_SOURCE,
    ENGINE_GPU_REQUIREMENT,
    ENGINE_VOICE_CLONING,
)
from agentscope.tts import DashScopeTTSModel, TTSModelCard


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
                "source": None,
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
            source="api",
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
                "source": "api",
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

    def test_migrate_legacy_voice_profile_id(self) -> None:
        """Legacy frontend metadata is promoted to the authorization field."""
        config = TTSModelConfig(
            type="openai_credential",
            credential_id="cred-1",
            model="gpt-4o-mini-tts",
            parameters={
                "voice": "voice-custom",
                "_voice_profile_id": "profile-1",
            },
        )
        self.assertEqual(config.voice_profile_id, "profile-1")
        self.assertNotIn("_voice_profile_id", config.parameters)


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
            source="api",
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

    async def test_qwen3_tts_vc_cannot_use_shared_class_default(self) -> None:
        """Qwen3 TTS VC requires a cloned voice instead of ``Cherry``."""
        card = next(
            card
            for card in DashScopeTTSModel.list_models()
            if card.name == "qwen3-tts-vc-2026-01-22"
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
                credential_owner_id="user-a",
                storage=storage,
                card=_model_card([], voice_cloning=True),
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("requires a cloned voice profile", ctx.exception.detail)
        storage.get_voice_profile.assert_not_called()

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
                credential_owner_id="user-a",
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


class TestEngineConstants(TestCase):
    """Unit tests for engine-level constant dicts."""

    def test_all_engines_have_source(self) -> None:
        """Every engine type has an ENGINE_SOURCE entry."""
        args = typing.get_args(_ENGINE_TYPE)
        for engine in args:
            self.assertIn(engine, ENGINE_SOURCE)

    def test_all_engines_have_gpu_requirement(self) -> None:
        """Every engine type has an ENGINE_GPU_REQUIREMENT."""
        args = typing.get_args(_ENGINE_TYPE)
        for engine in args:
            self.assertIn(engine, ENGINE_GPU_REQUIREMENT)

    def test_all_engines_have_voice_cloning_flag(self) -> None:
        """Every engine type has an ENGINE_VOICE_CLONING."""
        args = typing.get_args(_ENGINE_TYPE)
        for engine in args:
            self.assertIn(engine, ENGINE_VOICE_CLONING)

    def test_api_engines_no_gpu(self) -> None:
        """API engines should not require GPU."""
        expected = {
            "cosyvoice": None,
            "dashscope_tts": None,
            "openai_tts": None,
            "gemini_tts": None,
        }
        self.assertEqual(ENGINE_GPU_REQUIREMENT, expected)

    def test_voice_cloning_flags(self) -> None:
        """Verify known cloning support flags."""
        expected = {
            "cosyvoice": True,
            "dashscope_tts": True,
            "openai_tts": True,
            "gemini_tts": False,
        }
        self.assertEqual(ENGINE_VOICE_CLONING, expected)
