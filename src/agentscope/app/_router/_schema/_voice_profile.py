# -*- coding: utf-8 -*-
"""Schema models for the voice profile router."""
from pydantic import BaseModel

from ...storage._model import VoiceProfileData, VoiceProfileRecord


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


class EngineInfo(BaseModel):
    """Metadata about a TTS engine."""

    name: str
    """The engine name (e.g. 'dashscope_tts')."""

    source: str
    """Whether the engine runs in cloud or locally."""

    gpu_requirement: str | None = None
    """GPU requirement for local engines."""

    voice_cloning: bool = False
    """Whether the engine supports voice cloning."""

    credentials: list[CredentialRef] = []
    """Available credentials for this engine."""


class AvailableEnginesResponse(BaseModel):
    """Response body for available TTS engines."""

    engines: list[str]
    """Sorted list of engine names with credentials."""

    engine_details: list[EngineInfo] = []
    """Engine metadata including source and cloning."""


class CloneVoiceRequest(BaseModel):
    """Request body for voice cloning."""

    engine: str
    """TTS engine to clone for."""

    model: str | None = None
    """Target model (required for DashScope, optional for OpenAI)."""

    audio_base64: str | None = None
    """Base64-encoded audio (for Qwen-TTS/OpenAI upload)."""

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


class CloneVoiceResponse(BaseModel):
    """Response body for voice cloning."""

    voice_id: str
    """The cloned voice identifier."""

    credential_id: str
    """The credential used for cloning (must match synthesis)."""


class OpenAIConsentRequest(BaseModel):
    """Request body for uploading an OpenAI consent recording."""

    name: str = "agentscope_consent"
    """Label for the consent recording."""

    language: str = "en-US"
    """BCP 47 language tag for the consent phrase."""

    audio_base64: str
    """Base64-encoded consent audio recording."""

    audio_filename: str = "consent.wav"
    """Original filename (used for MIME detection)."""


class OpenAIConsentResponse(BaseModel):
    """Response body for OpenAI consent upload."""

    consent_id: str
    """The consent recording identifier (cons_xxx)."""
