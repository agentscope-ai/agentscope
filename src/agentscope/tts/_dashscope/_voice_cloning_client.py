# -*- coding: utf-8 -*-
"""DashScope voice enrollment for Qwen-TTS and CosyVoice."""
import base64
from typing import Any

from pydantic import BaseModel, Field

from ._endpoints import _dashscope_url, _qwen_http_url
from ...credential import DashScopeCredential
from .._voice_cloning_client_base import VoiceCloningClientBase
from .._voice_utils import (
    VoiceCloningError,
    _audio_file,
    _post_voice_request,
)

_CLONE_PATH = "/api/v1/services/audio/tts/customization"


class DashScopeVoiceCloningClient(VoiceCloningClientBase):
    """Create custom voices using the credential's DashScope endpoint.

    The credential's ``base_url`` selects the host and optional proxy prefix.
    Its API suffix is replaced with the audio enrollment path.
    Qwen-TTS uses the SDK global HTTP endpoint when ``base_url`` is omitted.
    Reuse the credential and target model for synthesis.
    Methods raise ``ValueError`` for invalid inputs and ``VoiceCloningError``
    for failed provider requests.
    """

    class Parameters(VoiceCloningClientBase.Parameters):
        """DashScope model and enrollment options."""

        target_model: str = Field(min_length=1)
        """Synthesis model supporting the enrolled voice."""

        prefix: str = Field(default="agentscope", min_length=1)
        """Provider-supported prefix for the voice name."""

        text: str | None = None
        """Optional transcript for the Qwen voice enrollment protocol."""

    credential: DashScopeCredential

    def __init__(self, credential: DashScopeCredential) -> None:
        """Bind the credential used for voice enrollment."""
        super().__init__(credential)

    @classmethod
    def get_enrollment_model(cls, model: str) -> str:
        """Resolve the enrollment model used for a synthesis model family."""
        if model.startswith(("cosyvoice-", "qwen-audio-")):
            return "voice-enrollment"
        if model.startswith("qwen"):
            return "qwen-voice-enrollment"
        raise ValueError(
            f"Unsupported DashScope voice model family: {model!r}",
        )

    async def create_voice(
        self,
        audio: bytes | None = None,
        *,
        filename: str = "voice.wav",
        audio_url: str | None = None,
        parameters: BaseModel | None = None,
    ) -> str:
        """Enroll a voice and return its provider ID.

        Args:
            audio: Encoded sample bytes for Qwen-TTS. Supply either this or
                ``audio_url``, never both.
            filename: Sample filename used to determine its MIME type.
            audio_url: Publicly accessible sample URL for CosyVoice.
            parameters: ``Parameters`` specifying the target model, prefix
                and optional transcript. Qwen Audio ignores the transcript.

        Returns:
            The voice ID to pass as the synthesis model's ``voice`` value.
        """
        options = self.Parameters.model_validate(
            parameters.model_dump() if parameters is not None else {},
        )
        target_model = options.target_model
        if (audio is None) == (audio_url is None):
            raise ValueError("Provide exactly one of audio or audio_url")
        enrollment = self.get_enrollment_model(target_model)

        inputs: dict[str, Any] = {
            "action": "create_voice",
            "target_model": target_model,
            "prefix": options.prefix,
        }
        if target_model.startswith("cosyvoice-"):
            if audio is not None:
                raise ValueError(
                    "CosyVoice requires audio_url, not audio bytes",
                )
            if not audio_url:
                raise ValueError("audio_url must not be empty")
            inputs["url"] = audio_url
        else:
            if audio is None:
                raise ValueError(
                    "Qwen-TTS requires audio bytes, not audio_url",
                )
            _, _, mime_type = _audio_file(filename, audio)
            encoded = base64.b64encode(audio).decode("ascii")
            data_uri = f"data:{mime_type};base64,{encoded}"
            if target_model.startswith("qwen-audio-"):
                inputs["audio"] = data_uri
            else:
                inputs = {
                    "action": "create",
                    "target_model": target_model,
                    "preferred_name": options.prefix,
                    "audio": {"data": data_uri},
                }
                if options.text:
                    inputs["text"] = options.text
        result = await _post_voice_request(
            _qwen_http_url(self.credential, _CLONE_PATH)
            if enrollment == "qwen-voice-enrollment"
            else _dashscope_url(self.credential.base_url, _CLONE_PATH),
            provider="DashScope",
            operation="voice clone",
            headers={
                "Authorization": (
                    f"Bearer {self.credential.api_key.get_secret_value()}"
                ),
                "Content-Type": "application/json",
            },
            json={"model": enrollment, "input": inputs},
        )
        output = result.get("output")
        voice_id = (
            output.get("voice")
            or output.get("voice_id")
            or output.get("voice_name")
            if isinstance(output, dict)
            else None
        )
        if not isinstance(voice_id, str) or not voice_id:
            raise VoiceCloningError(
                "DashScope returned success but no "
                f"voice found in response: {str(result)[:500]}",
            )
        return voice_id
