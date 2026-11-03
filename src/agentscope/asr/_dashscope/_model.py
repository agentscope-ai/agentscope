# -*- coding: utf-8 -*-
"""DashScope non-realtime speech recognition implementation."""

import asyncio
import base64
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ...credential import DashScopeCredential
from ...message import TextBlock
from .._asr_base import ASRModelBase
from .._asr_response import ASRResponse


_AUDIO_FORMATS = {
    ".aac": ("aac", "audio/aac"),
    ".amr": ("amr", "audio/amr"),
    ".flac": ("flac", "audio/flac"),
    ".m4a": ("m4a", "audio/mp4"),
    ".mp3": ("mp3", "audio/mpeg"),
    ".mp4": ("mp4", "audio/mp4"),
    ".mpeg": ("mpeg", "audio/mpeg"),
    ".ogg": ("ogg", "audio/ogg"),
    ".opus": ("opus", "audio/opus"),
    ".wav": ("wav", "audio/wav"),
    ".webm": ("webm", "audio/webm"),
}

_UNSUPPORTED_PARAMETERS = {
    "qwen-audio-3.0-asr-flash": {
        "keep_dialect",
        "speaker_diarization_enabled",
    },
}


class DashScopeASRModel(ASRModelBase):
    """Batch ASR through DashScope's native multimodal API."""

    class Parameters(BaseModel):
        """Options supported by Qwen Audio ASR Flash models."""

        language_hints: list[str] | None = Field(
            default=None,
            max_length=4,
            description=(
                "Optional language codes. Up to four languages are supported."
            ),
        )
        keep_dialect: bool = Field(
            default=False,
            description="Preserve dialect expressions in the transcript.",
        )
        speaker_diarization_enabled: bool = Field(
            default=False,
            description="Identify different speakers in the recording.",
        )
        vocabulary: dict[str, int] | None = Field(
            default=None,
            description="Inline hotwords mapped to their recognition weights.",
        )
        sample_rate: int | None = Field(
            default=None,
            gt=0,
            description="Optional audio sample rate in Hz.",
        )

    type: Literal["dashscope_asr"] = "dashscope_asr"
    realtime: bool = False

    def __init__(
        self,
        credential: DashScopeCredential,
        model: str = "qwen-audio-3.1-asr-flash",
        parameters: "DashScopeASRModel.Parameters | None" = None,
    ) -> None:
        """Initialize a DashScope batch ASR model.

        Args:
            credential (`DashScopeCredential`):
                The credential used to authenticate with DashScope.
            model (`str`, defaults to
            ``"qwen-audio-3.1-asr-flash"``):
                The DashScope batch ASR model name.
            parameters (`DashScopeASRModel.Parameters | None`, defaults to
            `None`):
                Provider-specific transcription parameters. Default
                parameters are used when omitted.
        """
        super().__init__(credential, model, parameters)
        self.api_key = credential.api_key.get_secret_value()
        self.base_address = self._resolve_base_address(credential.base_url)

    async def transcribe(
        self,
        audio: bytes | None = None,
        filename: str = "audio.wav",
        **kwargs: Any,
    ) -> ASRResponse:
        """Transcribe a complete audio recording without streaming.

        Args:
            audio (`bytes | None`, defaults to `None`):
                The complete encoded audio recording.
            filename (`str`, defaults to ``"audio.wav"``):
                The filename used to determine the audio format and MIME
                type.
            **kwargs (`Any`):
                Additional options passed to the DashScope API.

        Returns:
            `ASRResponse`:
                The final recognized text.

        Raises:
            `ValueError`:
                If the audio is empty or its filename has an unsupported
                extension.
            `RuntimeError`:
                If DashScope rejects the request or omits the transcript.
        """
        if not audio:
            raise ValueError("audio must not be empty")
        if not filename:
            raise ValueError("filename must not be empty")

        audio_format, media_type = self._resolve_audio_format(filename)
        encoded_audio = base64.b64encode(audio).decode("ascii")
        data_uri = f"data:{media_type};base64,{encoded_audio}"
        options = self.parameters.model_dump(exclude_none=True)
        options.update(kwargs)
        for parameter in _UNSUPPORTED_PARAMETERS.get(self.model, set()):
            options.pop(parameter, None)
        options["format"] = options.get("format", audio_format)

        import dashscope

        response = await asyncio.to_thread(
            dashscope.MultiModalConversation.call,
            api_key=self.api_key,
            base_address=self.base_address,
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": data_uri},
                        },
                    ],
                },
            ],
            stream=False,
            **options,
        )
        if response.status_code != 200:
            raise RuntimeError(f"DashScope ASR API error: {response}")

        output = response.output
        text = (
            output.get("text")
            if isinstance(output, dict)
            else getattr(output, "text", None)
        )
        if not isinstance(text, str):
            raise RuntimeError("DashScope ASR response did not contain text")
        return ASRResponse(content=TextBlock(text=text))

    @staticmethod
    def _resolve_audio_format(filename: str) -> tuple[str, str]:
        """Return the DashScope format and MIME type for a filename."""
        suffix = Path(filename).suffix.lower()
        result = _AUDIO_FORMATS.get(suffix)
        if result is None:
            raise ValueError(
                f"Unsupported audio file format: {suffix or filename}",
            )
        return result

    @staticmethod
    def _resolve_base_address(base_url: str) -> str:
        """Convert a compatible endpoint into its native API counterpart."""
        normalized = base_url.rstrip("/")
        compatible_suffix = "/compatible-mode/v1"
        if normalized.endswith(compatible_suffix):
            return f"{normalized[:-len(compatible_suffix)]}/api/v1"
        return normalized
