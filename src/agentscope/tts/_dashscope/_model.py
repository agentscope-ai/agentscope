# -*- coding: utf-8 -*-
"""DashScope TTS model implementation using MultiModalConversation API."""
import base64
import io
import wave
from datetime import datetime
from typing import (
    Any,
    AsyncGenerator,
    Generator,
    Literal,
    TYPE_CHECKING,
)

from pydantic import BaseModel, Field

from .._tts_base import TTSModelBase
from .._tts_response import TTSResponse, TTSUsage
from ..._utils._audio import _build_streaming_wav_header
from ...credential import DashScopeCredential
from ...message import DataBlock, Base64Source

if TYPE_CHECKING:
    from dashscope.api_entities.dashscope_response import (
        MultiModalConversationResponse,
    )


# DashScope TTS returns raw PCM (24kHz, mono, 16-bit). We wrap it as WAV
# on the way out so the frontend can play it: streaming deltas get a
# streaming WAV header on the first chunk; non-streaming returns a
# self-contained fixed-size WAV.
_TTS_SAMPLE_RATE = 24000
_TTS_CHANNELS = 1
_TTS_BITS_PER_SAMPLE = 16
_DEFAULT_MEDIA_TYPE = "audio/wav"
_SENTINEL = object()


def _parse_usage(usage: Any, elapsed: float) -> TTSUsage | None:
    """Extract a TTSUsage from the DashScope usage object, or None."""
    if usage is None:
        return None
    return TTSUsage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        time=elapsed,
    )


class DashScopeTTSModel(TTSModelBase):
    """DashScope TTS model implementation using the MultiModalConversation
    API. For more details please see the `official document
    <https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2879134>`_.
    """

    class Parameters(BaseModel):
        """Frontend-exposed parameters for DashScope TTS models."""

        voice: str = Field(
            default="Cherry",
            title="Voice",
            description="The voice to use for synthesis.",
        )

    type: Literal["dashscope_tts"] = "dashscope_tts"
    """The type of the TTS model."""

    realtime: bool = False

    def __init__(
        self,
        credential: DashScopeCredential,
        model: str = "qwen3-tts-flash",
        parameters: "DashScopeTTSModel.Parameters | None" = None,
        stream: bool = True,
    ) -> None:
        """Initialize the DashScope TTS model.

        .. note:: More details about the parameters, such as ``model``
         and ``voice``, can be found in the `official document
         <https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2879134>`_.

        Args:
            credential (`DashScopeCredential`):
                The DashScope credential used to authenticate the API call.
            model (`str`, defaults to ``"qwen3-tts-flash"``):
                The TTS model name. Supported models include
                ``qwen3-tts-flash``, ``qwen-tts``, etc.
            parameters (`DashScopeTTSModel.Parameters | None`, defaults to \
            `None`):
                The TTS parameters (voice, language, etc.). When ``None``,
                the default parameters will be used.
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
        """Call the DashScope TTS API to synthesize speech from text.

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

        import dashscope

        response = dashscope.MultiModalConversation.call(
            model=self.model,
            api_key=self.credential.api_key.get_secret_value(),
            text=text,
            voice=self.parameters.voice,
            stream=True,
            **kwargs,
        )

        if self.stream:
            return self._parse_into_async_generator(response)

        return self._aggregate_sync(response)

    @staticmethod
    def _aggregate_sync(
        response: Generator["MultiModalConversationResponse", None, None],
    ) -> TTSResponse:
        """Aggregate all streaming chunks into a single self-contained WAV."""
        start_datetime = datetime.now()
        audio_bytes = bytearray()
        usage = None
        for chunk in response:
            if chunk.usage is not None:
                usage = chunk.usage
            if chunk.output is not None:
                audio = chunk.output.audio
                if audio and audio.data:
                    audio_bytes += base64.b64decode(audio.data)
        elapsed = (datetime.now() - start_datetime).total_seconds()

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
            usage=_parse_usage(usage, elapsed),
        )

    @staticmethod
    async def _parse_into_async_generator(
        response: Generator["MultiModalConversationResponse", None, None],
    ) -> AsyncGenerator[TTSResponse, None]:
        """Parse the streaming TTS response into an async generator.

        Each yielded ``TTSResponse`` carries an **incremental** WAV chunk:
        the first chunk is prefixed with a streaming WAV/RIFF header so the
        frontend can start playback immediately (without waiting for
        end-of-stream); subsequent chunks are raw PCM bytes appended to that
        open stream. The final response has ``is_last=True``.

        Args:
            response (`Generator[MultiModalConversationResponse, None, None]`):
                The streaming response from the DashScope TTS API.

        Yields:
            `TTSResponse`:
                A ``TTSResponse`` for each incremental audio chunk; the final
                response has ``is_last=True``.
        """
        pending: TTSResponse | None = None
        header_sent = False
        usage = None
        start_datetime = datetime.now()
        it = iter(response)
        while True:
            chunk = next(it, _SENTINEL)
            if chunk is _SENTINEL:
                break
            if chunk.usage is not None:
                usage = chunk.usage
            if chunk.output is None:
                continue
            audio = chunk.output.audio
            if not audio or not audio.data:
                continue
            delta_bytes = base64.b64decode(audio.data)
            if not delta_bytes:
                continue
            if not header_sent:
                payload = (
                    _build_streaming_wav_header(
                        sample_rate=_TTS_SAMPLE_RATE,
                        channels=_TTS_CHANNELS,
                        bits_per_sample=_TTS_BITS_PER_SAMPLE,
                    )
                    + delta_bytes
                )
                header_sent = True
            else:
                payload = delta_bytes
            if pending is not None:
                yield pending
            pending = TTSResponse(
                content=DataBlock(
                    source=Base64Source(
                        data=base64.b64encode(payload).decode("ascii"),
                        media_type=_DEFAULT_MEDIA_TYPE,
                    ),
                ),
                is_last=False,
            )
        elapsed = (datetime.now() - start_datetime).total_seconds()

        if pending is not None:
            pending.is_last = True
            pending.usage = _parse_usage(usage, elapsed)
            yield pending
        else:
            yield TTSResponse(
                content=None,
                is_last=True,
                usage=_parse_usage(usage, elapsed),
            )
