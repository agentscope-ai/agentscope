# -*- coding: utf-8 -*-
"""Gemini TTS model implementation using the ``generateContent`` API."""
import base64
import io
import wave
from datetime import datetime
from typing import (
    Any,
    AsyncGenerator,
    AsyncIterator,
    Literal,
    TYPE_CHECKING,
)

from pydantic import BaseModel, Field

from .._tts_base import TTSModelBase
from .._tts_response import TTSResponse, TTSUsage
from ..._utils._audio import _build_streaming_wav_header
from ...credential import GeminiCredential
from ...message import DataBlock, Base64Source

if TYPE_CHECKING:
    from google.genai.types import GenerateContentResponse


# Gemini TTS returns raw PCM (24kHz, mono, 16-bit). We wrap it as a
# self-contained WAV so the frontend can play it directly.
_TTS_SAMPLE_RATE = 24000
_TTS_CHANNELS = 1
_TTS_BITS_PER_SAMPLE = 16
_DEFAULT_MEDIA_TYPE = "audio/wav"


def _parse_usage(
    usage_metadata: Any,
    elapsed: float,
) -> TTSUsage | None:
    """Extract a TTSUsage from the Gemini ``usage_metadata`` object, or
    ``None``."""
    if not usage_metadata:
        return None
    return TTSUsage(
        input_tokens=getattr(usage_metadata, "prompt_token_count", 0) or 0,
        output_tokens=getattr(usage_metadata, "candidates_token_count", 0)
        or 0,
        time=elapsed,
    )


class GeminiTTSModel(TTSModelBase):
    """Gemini TTS model implementation using the ``generateContent`` API
    with ``responseModalities: ["AUDIO"]``. For more details please see
    the `official document
    <https://ai.google.dev/gemini-api/docs/speech-generation>`_.
    """

    class Parameters(BaseModel):
        """Frontend-exposed parameters for Gemini TTS models."""

        voice: str = Field(
            default="Kore",
            title="Voice",
            description="The voice to use for synthesis.",
        )

    type: Literal["gemini_tts"] = "gemini_tts"
    """The type of the TTS model."""

    realtime: bool = False

    def __init__(
        self,
        credential: GeminiCredential,
        model: str = "gemini-2.5-flash-preview-tts",
        parameters: "GeminiTTSModel.Parameters | None" = None,
        stream: bool = False,
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the Gemini TTS model.

        .. note:: More details about the parameters, such as ``model``
         and ``voice``, can be found in the `official document
         <https://ai.google.dev/gemini-api/docs/speech-generation>`_.

        Args:
            credential (`GeminiCredential`):
                The Google Gemini credential used to authenticate the API
                call.
            model (`str`, defaults to ``"gemini-2.5-flash-preview-tts"``):
                The TTS model name. Supported models include
                ``gemini-2.5-flash-preview-tts`` and
                ``gemini-2.5-pro-preview-tts``.
            parameters (`GeminiTTSModel.Parameters | None`, defaults to \
            `None`):
                The TTS parameters (voice, etc.). When ``None``, the default
                parameters will be used.
            stream (`bool`, defaults to `False`):
                Whether to use streaming output. When `True`,
                :meth:`synthesize` returns an async generator yielding
                incremental ``TTSResponse`` chunks (see the `streaming
                document
                <https://ai.google.dev/gemini-api/docs/speech-generation#streaming>`_);
                when `False`, it returns a single aggregated
                ``TTSResponse``.
            client_kwargs (`dict[str, Any] | None`, defaults to `None`):
                Extra keyword arguments forwarded to ``google.genai.Client``
                (e.g. ``vertexai``, ``project``, ``location``,
                ``credentials``, ``http_options``).
        """
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            stream=stream,
        )
        self.client_kwargs = client_kwargs or {}

    async def synthesize(
        self,
        text: str | None = None,
        **kwargs: Any,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        """Call the Gemini TTS API to synthesize speech from text.

        Args:
            text (`str | None`, optional):
                The text to be synthesized.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the
                ``generate_content`` API call's ``config``.

        Returns:
            `TTSResponse | AsyncGenerator[TTSResponse, None]`:
                A single ``TTSResponse`` when ``stream=False``, or an async
                generator yielding incremental ``TTSResponse`` chunks when
                ``stream=True``.
        """
        if not text:
            return TTSResponse(content=None)

        from google import genai

        client = genai.Client(
            **{
                "api_key": self.credential.api_key.get_secret_value(),
                **self.client_kwargs,
            },
        )

        config: dict[str, Any] = {
            "response_modalities": ["AUDIO"],
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {
                        "voice_name": self.parameters.voice,
                    },
                },
            },
            **kwargs,
        }

        if self.stream:
            stream = await client.aio.models.generate_content_stream(
                model=self.model,
                contents=text,
                config=config,
            )
            return self._parse_stream_into_async_generator(stream)

        start_datetime = datetime.now()
        response = await client.aio.models.generate_content(
            model=self.model,
            contents=text,
            config=config,
        )
        elapsed = (datetime.now() - start_datetime).total_seconds()

        return self._parse_response(response, elapsed)

    @staticmethod
    def _parse_response(
        response: "GenerateContentResponse",
        elapsed: float,
    ) -> TTSResponse:
        """Parse the Gemini ``generateContent`` response into a
        ``TTSResponse`` carrying a self-contained WAV file.

        Args:
            response (`GenerateContentResponse`):
                The response from the Gemini ``generateContent`` API.
            elapsed (`float`):
                The time elapsed (in seconds) for the API call.

        Returns:
            `TTSResponse`:
                A ``TTSResponse`` containing the synthesized audio, or an
                empty response if no audio was returned.
        """
        usage = _parse_usage(
            getattr(response, "usage_metadata", None),
            elapsed,
        )

        audio_bytes = bytearray()
        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) or []
            for part in parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data and inline_data.data:
                    data = inline_data.data
                    # `inline_data.data` may be a base64-encoded `str` or
                    # raw `bytes` depending on the installed google-genai
                    # SDK version, hence the defensive isinstance check.
                    if isinstance(data, str):
                        data = base64.b64decode(data)
                    audio_bytes += data

        if not audio_bytes:
            return TTSResponse(content=None, usage=usage)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(_TTS_CHANNELS)
            wav.setsampwidth(_TTS_BITS_PER_SAMPLE // 8)
            wav.setframerate(_TTS_SAMPLE_RATE)
            wav.writeframes(bytes(audio_bytes))

        return TTSResponse(
            content=DataBlock(
                source=Base64Source(
                    data=base64.b64encode(buf.getvalue()).decode("ascii"),
                    media_type=_DEFAULT_MEDIA_TYPE,
                ),
            ),
            usage=usage,
        )

    @staticmethod
    async def _parse_stream_into_async_generator(
        stream: AsyncIterator["GenerateContentResponse"],
    ) -> AsyncGenerator[TTSResponse, None]:
        """Parse the streaming ``generateContent`` response into an async
        generator.

        Each yielded ``TTSResponse`` carries an **incremental** WAV chunk:
        the first chunk is prefixed with a streaming WAV/RIFF header so the
        frontend can start playback immediately (without waiting for
        end-of-stream); subsequent chunks are raw PCM bytes appended to that
        open stream. The final response has ``is_last=True``.

        Args:
            stream (`AsyncIterator[GenerateContentResponse]`):
                The streaming response from the Gemini ``generateContent``
                API.

        Yields:
            `TTSResponse`:
                A ``TTSResponse`` for each incremental audio chunk; the
                final response has ``is_last=True``.
        """
        pending: TTSResponse | None = None
        header_sent = False
        usage_metadata = None
        start_datetime = datetime.now()

        async for chunk in stream:
            chunk_usage = getattr(chunk, "usage_metadata", None)
            if chunk_usage is not None:
                usage_metadata = chunk_usage

            candidates = getattr(chunk, "candidates", None) or []
            for candidate in candidates:
                content = getattr(candidate, "content", None)
                parts = getattr(content, "parts", None) or []
                for part in parts:
                    inline_data = getattr(part, "inline_data", None)
                    if not inline_data or not inline_data.data:
                        continue
                    data = inline_data.data
                    # `inline_data.data` may be a base64-encoded `str` or
                    # raw `bytes` depending on the installed google-genai
                    # SDK version, hence the defensive isinstance check.
                    if isinstance(data, str):
                        data = base64.b64decode(data)
                    if not data:
                        continue

                    if not header_sent:
                        payload = (
                            _build_streaming_wav_header(
                                sample_rate=_TTS_SAMPLE_RATE,
                                channels=_TTS_CHANNELS,
                                bits_per_sample=_TTS_BITS_PER_SAMPLE,
                            )
                            + data
                        )
                        header_sent = True
                    else:
                        payload = data

                    if pending is not None:
                        yield pending
                    pending = TTSResponse(
                        content=DataBlock(
                            source=Base64Source(
                                data=base64.b64encode(payload).decode(
                                    "ascii",
                                ),
                                media_type=_DEFAULT_MEDIA_TYPE,
                            ),
                        ),
                        is_last=False,
                    )

        elapsed = (datetime.now() - start_datetime).total_seconds()

        if pending is not None:
            pending.is_last = True
            pending.usage = _parse_usage(usage_metadata, elapsed)
            yield pending
        else:
            yield TTSResponse(
                content=None,
                is_last=True,
                usage=_parse_usage(usage_metadata, elapsed),
            )
