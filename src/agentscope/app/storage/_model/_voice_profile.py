# -*- coding: utf-8 -*-
"""The voice profile storage model."""
from typing import Literal

from pydantic import BaseModel, Field

from ...._utils._common import _generate_id
from ._base import _RecordBase


_ENGINE_TYPE = Literal[
    "cosyvoice",
    "dashscope_tts",
    "openai_tts",
    "gemini_tts",
]

_SOURCE_TYPE = Literal["api", "local"]

ENGINE_TO_CREDENTIAL_TYPE: dict[str, str] = {
    "cosyvoice": "dashscope_credential",
    "dashscope_tts": "dashscope_credential",
    "openai_tts": "openai_credential",
    "gemini_tts": "gemini_credential",
}

ENGINE_SOURCE: dict[str, _SOURCE_TYPE] = {
    "cosyvoice": "api",
    "dashscope_tts": "api",
    "openai_tts": "api",
    "gemini_tts": "api",
}

ENGINE_GPU_REQUIREMENT: dict[str, str | None] = {
    "cosyvoice": None,
    "dashscope_tts": None,
    "openai_tts": None,
    "gemini_tts": None,
}

ENGINE_VOICE_CLONING: dict[str, bool] = {
    "cosyvoice": True,
    "dashscope_tts": True,
    "openai_tts": True,
    "gemini_tts": False,
}


class VoiceProfileData(BaseModel):
    """The voice profile data model."""

    name: str = Field(
        description="Display name for this voice profile.",
        title="Name",
    )

    engine: _ENGINE_TYPE | None = Field(
        default=None,
        description=(
            "Preferred TTS engine: cosyvoice, "
            "dashscope_tts, openai_tts, or gemini_tts."
        ),
        title="Engine",
    )

    model: str | None = Field(
        default=None,
        description=(
            "Specific TTS model name for synthesis "
            "(e.g. 'qwen3-tts-flash', 'cosyvoice-v3-flash')."
        ),
        title="Model",
    )

    credential_id: str | None = Field(
        default=None,
        description=(
            "Credential ID used for voice cloning and "
            "synthesis. Ensures the same API key is used "
            "for both operations."
        ),
        title="Credential ID",
    )

    source: _SOURCE_TYPE | None = Field(
        default=None,
        description=(
            "Deployment source: 'api' for cloud-based "
            "or 'local' for on-device inference."
        ),
        title="Source",
    )

    voice: str | None = Field(
        default=None,
        description=(
            "Voice identifier: preset name (e.g. 'alloy')"
            " or cloned voice ID from API."
        ),
        title="Voice",
    )

    metadata: dict | None = Field(
        default=None,
        description="Engine-specific extra configuration.",
        title="Metadata",
    )


class VoiceProfileRecord(_RecordBase):
    """The voice profile ORM model."""

    user_id: str = Field(
        default_factory=_generate_id,
    )
    """The owner user id."""

    data: VoiceProfileData
    """The voice profile data."""
