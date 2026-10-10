# -*- coding: utf-8 -*-
"""OpenAI Audio Transcriptions API implementation."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from ...credential import OpenAICredential
from ...message import TextBlock
from .._asr_base import ASRModelBase
from .._asr_response import ASRResponse


class OpenAIASRModel(ASRModelBase):
    """Batch speech recognition using OpenAI's Audio Transcriptions API."""

    class Parameters(BaseModel):
        """Options supported by OpenAI transcription models."""

        language: str | None = Field(
            default=None,
            description="Optional ISO-639-1 language code for the audio.",
        )
        prompt: str | None = Field(
            default=None,
            description="Optional transcription guidance.",
        )

    type: Literal["openai_asr"] = "openai_asr"
    """The model implementation type discriminator."""

    realtime: bool = False
    """OpenAI Audio Transcriptions accepts complete audio files."""

    def __init__(
        self,
        credential: OpenAICredential,
        model: str = "gpt-4o-mini-transcribe",
        parameters: "OpenAIASRModel.Parameters | None" = None,
    ) -> None:
        super().__init__(credential, model, parameters)

        import openai

        self.client: openai.AsyncClient = openai.AsyncClient(
            api_key=credential.api_key.get_secret_value(),
            organization=credential.organization,
            base_url=credential.base_url,
        )

    async def transcribe(
        self,
        audio: bytes,
        filename: str = "audio.wav",
        **kwargs: Any,
    ) -> ASRResponse:
        """Transcribe in-memory audio, using ``filename`` for its format."""
        if not audio:
            raise ValueError("audio must not be empty")
        if not filename:
            raise ValueError("filename must not be empty")

        options = self.parameters.model_dump(exclude_none=True)
        result = await self.client.audio.transcriptions.create(
            file=(filename, audio),
            model=self.model,
            response_format="json",
            **options,
            **kwargs,
        )
        return ASRResponse(content=TextBlock(text=result.text))
