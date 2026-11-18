# -*- coding: utf-8 -*-
"""Schema models for the voice profile router."""
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from pydantic_core import PydanticCustomError

from ...storage._model import VoiceProfileData, VoiceProfileRecord
from ....tts import TTSModelCard
from ....tts._minimax._voice_cloning_client import MAX_CLONE_AUDIO_SIZE_BYTES

MAX_AUDIO_SIZE_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BASE64_LENGTH = 4 * ((MAX_AUDIO_SIZE_BYTES + 2) // 3)
MAX_MINIMAX_BASE64_LENGTH = 4 * ((MAX_CLONE_AUDIO_SIZE_BYTES + 2) // 3)


class CreateVoiceProfileRequest(BaseModel):
    """Request body for creating a voice profile."""

    data: VoiceProfileData
    """The voice profile configuration."""


class CreateVoiceProfileResponse(BaseModel):
    """Response body for creating a voice profile."""

    profile_id: str
    """The created profile's unique identifier."""


class ListVoiceProfilesResponse(BaseModel):
    """Response body for listing voice profiles."""

    profiles: list[VoiceProfileRecord]
    """All voice profiles for the user."""

    total: int
    """Total count of profiles."""


class UpdateVoiceProfileRequest(BaseModel):
    """Request body for updating a voice profile."""

    data: VoiceProfileData
    """The updated voice profile configuration."""


class CredentialRef(BaseModel):
    """Minimal credential reference for engine binding."""

    id: str
    """Credential ID."""

    label: str
    """Display label (credential name or type)."""


class EngineModelInfo(TTSModelCard):
    """A selectable synthesis model and its enrollment model, if any."""

    enrollment_model: str | None = None
    """Separate model used for voice enrollment, supplied by the SDK client."""


class EngineInfo(BaseModel):
    """Metadata about a TTS engine."""

    name: str
    """The engine name (e.g. 'dashscope_tts')."""

    credential_type: str
    """Credential provider required by the engine."""

    models: list[EngineModelInfo] = []
    """Synthesis model cards belonging to this engine."""

    voice_cloning: bool = False
    """Whether the engine supports voice cloning."""

    credentials: list[CredentialRef] = []
    """Available credentials for this engine."""


class AvailableEnginesResponse(BaseModel):
    """Response body for available TTS engines."""

    engine_details: list[EngineInfo] = []
    """Available engines with credentials and capabilities, sorted by name."""


class CloneVoiceRequest(BaseModel):
    """Request body for voice cloning."""

    engine: str
    """TTS engine to clone for."""

    credential_id: str
    """Owner-scoped credential used for cloning and later synthesis."""

    model: str | None = None
    """Target model (required for DashScope, optional for OpenAI)."""

    audio_base64: str | None = Field(
        default=None,
        max_length=MAX_MINIMAX_BASE64_LENGTH,
    )
    """Base64-encoded audio for providers supporting file uploads."""

    audio_filename: str = "voice.wav"
    """Original filename (used for MIME detection)."""

    audio_url: str | None = None
    """Publicly accessible audio URL (for CosyVoice)."""

    text: str | None = None
    """Audio transcript (improves Qwen-TTS quality)."""

    prefix: str = "agentscope"
    """Prefix for the generated voice name."""

    consent: str | None = None
    """OpenAI consent token (required for OpenAI cloning)."""

    @field_validator("audio_base64")
    @classmethod
    def validate_audio_size(
        cls,
        value: str | None,
        info: ValidationInfo,
    ) -> str | None:
        """Keep the smaller upload limit for other providers."""
        if (
            info.data.get("engine") != "minimax_tts"
            and value is not None
            and len(value) > MAX_AUDIO_BASE64_LENGTH
        ):
            raise PydanticCustomError(
                "string_too_long",
                "String should have at most {max_length} characters",
                {"max_length": MAX_AUDIO_BASE64_LENGTH},
            )
        return value


class CloneVoiceResponse(BaseModel):
    """Response body for voice cloning."""

    voice_id: str
    """The cloned voice identifier."""

    credential_id: str
    """The credential used for cloning (must match synthesis)."""


class OpenAIConsentRequest(BaseModel):
    """Request body for uploading an OpenAI consent recording."""

    credential_id: str
    """Owner-scoped OpenAI credential used for the consent recording."""

    name: str = "agentscope_consent"
    """Label for the consent recording."""

    language: str = "en-US"
    """BCP 47 language tag for the consent phrase."""

    audio_base64: str = Field(max_length=MAX_AUDIO_BASE64_LENGTH)
    """Base64-encoded consent audio recording."""

    audio_filename: str = "consent.wav"
    """Original filename (used for MIME detection)."""


class OpenAIConsentResponse(BaseModel):
    """Response body for OpenAI consent upload."""

    consent_id: str
    """The consent recording identifier (cons_xxx)."""
