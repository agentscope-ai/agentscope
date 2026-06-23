# -*- coding: utf-8 -*-
"""OpenAI TTS model implementation using the Audio Speech API."""
import base64
from datetime import datetime
from typing import (
    Any,
    AsyncGenerator,
    Literal,
    TYPE_CHECKING,
)

from pydantic import BaseModel, Field

from .._tts_base import TTSModelBase
from .._tts_response import TTSResponse, TTSUsage
from ...credential import OpenAICredential
from ...message import DataBlock, Base64Source

if TYPE_CHECKING:
    from openai import AsyncOpenAI


# Map the OpenAI ``response_format`` values to their MIME media types.
_MEDIA_TYPES = {
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
}
_DEFAULT_RESPONSE_FORMAT = "mp3"


def _parse_usage(usage: Any, elapsed: float) -> TTSUsage | None:
    """Extract a TTSUsage from the OpenAI usage object, or None."""
    if usage is None:
        return None
    return TTSUsage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        time=elapsed,
    )


class OpenAITTSModel(TTSModelBase):
    """OpenAI TTS model implementation using the Audio Speech API. For more
    details please see the `official document
    <https://platform.openai.com/docs/guides/text-to-speech>`_.
    """

    class Parameters(BaseModel):
        """Frontend-exposed parameters for OpenAI TTS models."""

        voice: str = Field(
            default="alloy",
            title="Voice",
            description="The voice to use for synthesis.",
        )

        response_format: Literal[
            "mp3",
            "opus",
            "aac",
            "flac",
            "wav",
            "pcm",
        ] = Field(
            default=_DEFAULT_RESPONSE_FORMAT,
            title="Response Format",
            description="The audio format of the synthesized speech.",
        )

        instructions: str | None = Field(
            default=None,
            title="Instructions",
            description=(
                "Additional instructions for controlling the voice "
                "(only supported by some models, e.g. gpt-4o-mini-tts)."
            ),
        )

    type: Literal["openai_tts"] = "openai_tts"
    """The type of the TTS model."""

    realtime: bool = False

    def __init__(
        self,
        credential: OpenAICredential,
        model: str = "tts-1",
        parameters: "OpenAITTSModel.Parameters | None" = None,
        stream: bool = True,
    ) -> None:
        """Initialize the OpenAI TTS model.

        .. note:: More details about the parameters, such as ``model``
         and ``voice``, can be found in the `official document
         <https://platform.openai.com/docs/guides/text-to-speech>`_.

        Args:
            credential (`OpenAICredential`):
                The OpenAI credential used to authenticate the API call.
            model (`str`, defaults to ``"tts-1"``):
                The TTS model name. Supported models include ``tts-1``,
                ``tts-1-hd`` and ``gpt-4o-mini-tts``.
            parameters (`OpenAITTSModel.Parameters | None`, defaults to \
            `None`):
                The TTS parameters (voice, response format, etc.). When
                ``None``, the default parameters will be used.
            stream (`bool`, defaults to `True`):
                Whether to use streaming output. When `True`,
                :meth:`synthesize` returns an async generator yielding
                ``TTSResponse`` chunks; when `False`, it returns a single
                aggregated ``TTSResponse``.
        """
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            stream=stream,
        )

    async def synthesize(
        self,
        text: str | None = None,
        **kwargs: Any,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        """Call the OpenAI Audio Speech API to synthesize speech from text.

        Args:
            text (`str | None`, optional):
                The text to be synthesized.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the TTS API call.

        Returns:
            `TTSResponse | AsyncGenerator[TTSResponse, None]`:
                A single ``TTSResponse`` when ``stream=False``, or an async
                generator yielding ``TTSResponse`` chunks when ``stream=True``.
        """
        if not text:
            return TTSResponse(content=None)

        import openai

        client = openai.AsyncClient(
            api_key=self.credential.api_key.get_secret_value(),
            organization=self.credential.organization,
            base_url=self.credential.base_url,
        )

        media_type = _MEDIA_TYPES.get(
            self.parameters.response_format,
            _MEDIA_TYPES[_DEFAULT_RESPONSE_FORMAT],
        )

        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "voice": self.parameters.voice,
            "input": text,
            "response_format": self.parameters.response_format,
            **kwargs,
        }
        if self.parameters.instructions:
            request_kwargs["instructions"] = self.parameters.instructions

        if self.stream:
            return self._stream(client, media_type, **request_kwargs)

        return await self._aggregate(client, media_type, **request_kwargs)

    @staticmethod
    async def _aggregate(
        client: "AsyncOpenAI",
        media_type: str,
        **request_kwargs: Any,
    ) -> TTSResponse:
        """Call the API and aggregate the full audio into a single
        ``TTSResponse``."""
        start_datetime = datetime.now()
        response = await client.audio.speech.create(**request_kwargs)
        audio_bytes = response.content
        elapsed = (datetime.now() - start_datetime).total_seconds()

        return TTSResponse(
            content=DataBlock(
                source=Base64Source(
                    data=base64.b64encode(audio_bytes).decode("ascii"),
                    media_type=media_type,
                ),
            ),
            usage=_parse_usage(None, elapsed),
        )

    @staticmethod
    async def _stream(
        client: "AsyncOpenAI",
        media_type: str,
        **request_kwargs: Any,
    ) -> AsyncGenerator[TTSResponse, None]:
        """Call the API and yield incremental audio chunks as
        ``TTSResponse`` objects.

        Args:
            client (`AsyncOpenAI`):
                The async OpenAI client.
            media_type (`str`):
                The media type of the synthesized audio.
            **request_kwargs (`Any`):
                Keyword arguments forwarded to the Audio Speech API.

        Yields:
            `TTSResponse`:
                A ``TTSResponse`` for each incremental audio chunk; the
                final response has ``is_last=True``.
        """
        start_datetime = datetime.now()
        pending: TTSResponse | None = None

        async with client.audio.speech.with_streaming_response.create(
            **request_kwargs,
        ) as response:
            async for delta_bytes in response.iter_bytes():
                if not delta_bytes:
                    continue
                if pending is not None:
                    yield pending
                pending = TTSResponse(
                    content=DataBlock(
                        source=Base64Source(
                            data=base64.b64encode(delta_bytes).decode(
                                "ascii",
                            ),
                            media_type=media_type,
                        ),
                    ),
                    is_last=False,
                )

        elapsed = (datetime.now() - start_datetime).total_seconds()

        if pending is not None:
            pending.is_last = True
            pending.usage = _parse_usage(None, elapsed)
            yield pending
        else:
            yield TTSResponse(
                content=None,
                is_last=True,
                usage=_parse_usage(None, elapsed),
            )
