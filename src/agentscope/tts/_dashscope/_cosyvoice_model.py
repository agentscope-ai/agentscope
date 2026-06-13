# -*- coding: utf-8 -*-
"""DashScope CosyVoice TTS model implementation."""
import asyncio
import base64
import queue
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

from .._tts_base import TTSModelBase
from .._tts_model_card import TTSModelCard
from .._tts_response import TTSResponse, TTSUsage
from ...credential import DashScopeCredential
from ...message import DataBlock, Base64Source
from ...types import JSONSerializableObject

if TYPE_CHECKING:
    from dashscope.audio.tts_v2 import AudioFormat, ResultCallback


_DEFAULT_AUDIO_FORMAT = "wav"
_DEFAULT_SAMPLE_RATE = 24000
_DEFAULT_TIMEOUT_MILLIS = 600000


class _StreamComplete:
    """Sentinel for callback-driven streaming completion."""


class _StreamError:
    """Sentinel for callback-driven streaming errors."""


def _media_type(audio_format: str, sample_rate: int) -> str:
    """Return the media type for a CosyVoice audio format."""
    audio_format = audio_format.lower()
    if audio_format == "wav":
        return "audio/wav"
    if audio_format == "mp3":
        return "audio/mpeg"
    if audio_format == "pcm":
        return f"audio/pcm;rate={sample_rate}"
    if audio_format == "opus":
        return "audio/ogg;codecs=opus"
    return f"audio/{audio_format}"


def _parse_usage(elapsed: float) -> TTSUsage:
    """Build a best-effort usage object when CosyVoice does not expose one."""
    return TTSUsage(
        input_tokens=0,
        output_tokens=0,
        time=elapsed,
    )


def _build_audio_response(
    audio_data: bytes,
    media_type: str,
    *,
    is_last: bool = True,
    metadata: dict[str, JSONSerializableObject] | None = None,
    usage: TTSUsage | None = None,
) -> TTSResponse:
    """Build a TTSResponse containing base64-encoded audio data."""
    return TTSResponse(
        content=DataBlock(
            source=Base64Source(
                data=base64.b64encode(audio_data).decode("ascii"),
                media_type=media_type,
            ),
        ),
        metadata=metadata,
        usage=usage,
        is_last=is_last,
    )


def _audio_format_enum(
    audio_format: str,
    sample_rate: int,
    bit_rate: int | None = None,
) -> "AudioFormat":
    """Resolve a CosyVoice output format to the SDK AudioFormat enum."""
    from dashscope.audio.tts_v2 import AudioFormat

    audio_format = audio_format.lower()
    if audio_format == "wav":
        enum_name = f"WAV_{sample_rate}HZ_MONO_16BIT"
    elif audio_format == "pcm":
        enum_name = f"PCM_{sample_rate}HZ_MONO_16BIT"
    elif audio_format == "mp3":
        bit_rate = bit_rate or (128 if sample_rate in (8000, 16000) else 256)
        enum_name = f"MP3_{sample_rate}HZ_MONO_{bit_rate}KBPS"
    elif audio_format == "opus":
        prefix = "OGG_OPUS"
        rate_label = (
            "8KHZ" if sample_rate == 8000 else f"{sample_rate // 1000}KHZ"
        )
        bit_rate = bit_rate or (32 if sample_rate == 8000 else 64)
        enum_name = f"{prefix}_{rate_label}_MONO_{bit_rate}KBPS"
    else:
        raise ValueError(f"Unsupported CosyVoice audio format: {audio_format}")

    try:
        return getattr(AudioFormat, enum_name)
    except AttributeError as exc:
        raise ValueError(
            f"Unsupported CosyVoice audio format/sample rate combination: "
            f"{audio_format}/{sample_rate}",
        ) from exc


def _response_metadata(
    request_id: str | None,
    response: Any,
) -> dict[str, JSONSerializableObject] | None:
    """Build metadata from the WebSocket SDK response."""
    metadata: dict[str, JSONSerializableObject] = {}
    if request_id is not None:
        metadata["request_id"] = request_id
    if response is not None:
        metadata["response"] = response
    return metadata or None


def _make_callback_class() -> type["ResultCallback"]:
    """Create a streaming callback class lazily after importing the SDK."""
    from dashscope.audio.tts_v2 import ResultCallback

    class _Callback(ResultCallback):
        """Callback that exposes streaming audio through a thread queue."""

        def __init__(self) -> None:
            super().__init__()
            self.queue: queue.Queue[tuple[Any, Any]] = queue.Queue()
            self._done = False

        def on_data(self, data: bytes) -> None:
            """Receive one binary audio chunk."""
            if data:
                self.queue.put((bytes, bytes(data)))

        def on_complete(self) -> None:
            """Mark the synthesis task as complete."""
            if not self._done:
                self._done = True
                self.queue.put((_StreamComplete, None))

        def on_close(self) -> None:
            """Close can be emitted after complete; only close once."""
            if not self._done:
                self._done = True
                self.queue.put((_StreamComplete, None))

        def on_error(self, message: Any) -> None:
            """Forward synthesis errors to the async consumer."""
            if not self._done:
                self._done = True
                self.queue.put((_StreamError, message))

    return _Callback


class DashScopeCosyVoiceTTSModel(TTSModelBase):
    """DashScope CosyVoice TTS model using the WebSocket SpeechSynthesizer."""

    class Parameters(BaseModel):
        """Frontend-exposed parameters for DashScope CosyVoice TTS models."""

        voice: str = Field(
            default="longanhuan",
            title="Voice",
            description="The voice to use for synthesis.",
        )

    type: Literal["dashscope_cosyvoice_tts"] = "dashscope_cosyvoice_tts"
    """The type of the TTS model."""

    realtime: bool = False

    def __init__(
        self,
        credential: DashScopeCredential,
        model: str = "cosyvoice-v3-flash",
        parameters: "DashScopeCosyVoiceTTSModel.Parameters | None" = None,
        stream: bool = True,
    ) -> None:
        """Initialize the DashScope CosyVoice TTS model.

        Args:
            credential (`DashScopeCredential`):
                The DashScope credential used to authenticate the API call.
            model (`str`, defaults to ``"cosyvoice-v3-flash"``):
                The CosyVoice model name.
            parameters (`DashScopeCosyVoiceTTSModel.Parameters | None`, \
            defaults to `None`):
                The TTS parameters. When ``None``, the default voice is used.
            stream (`bool`, defaults to `True`):
                Whether :meth:`synthesize` returns an async generator yielding
                ``TTSResponse`` chunks. When ``False``, audio is aggregated.
        """
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            stream=stream,
        )

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
    ) -> list[TTSModelCard]:
        """List CosyVoice model cards from the dedicated card directory."""
        if custom_yaml_dir is None:
            custom_yaml_dir = str(Path(__file__).parent / "_cosyvoice_models")
        return super().list_models(custom_yaml_dir)

    async def synthesize(
        self,
        text: str | None = None,
        **kwargs: Any,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        """Call the DashScope CosyVoice TTS API to synthesize speech.

        Args:
            text (`str | None`, optional):
                The text to be synthesized.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the CosyVoice API.
                ``voice``, ``audio_format``/``format`` and ``sample_rate`` can
                be overridden per call.

        Returns:
            `TTSResponse | AsyncGenerator[TTSResponse, None]`:
                A single aggregated response when ``stream=False``, or an
                async generator yielding incremental chunks when
                ``stream=True``.
        """
        if not text:
            return TTSResponse(content=None)

        import dashscope

        audio_format = kwargs.pop("audio_format", None)
        format_alias = kwargs.pop("format", None)
        if audio_format is None:
            audio_format = format_alias or _DEFAULT_AUDIO_FORMAT

        sample_rate = kwargs.pop("sample_rate", _DEFAULT_SAMPLE_RATE)
        voice = kwargs.pop("voice", self.parameters.voice)
        api_key = kwargs.pop(
            "api_key",
            self.credential.api_key.get_secret_value(),
        )
        timeout_millis = kwargs.pop(
            "timeout_millis",
            _DEFAULT_TIMEOUT_MILLIS,
        )
        bit_rate = kwargs.pop("bit_rate", None)
        media_type = _media_type(audio_format, sample_rate)
        format_enum = _audio_format_enum(audio_format, sample_rate, bit_rate)

        dashscope.api_key = api_key

        if self.stream:
            return self._parse_into_async_generator(
                text,
                self.model,
                voice,
                format_enum,
                media_type,
                timeout_millis,
                kwargs,
            )

        return await asyncio.to_thread(
            self._synthesize_sync,
            text=text,
            model=self.model,
            voice=voice,
            format_enum=format_enum,
            media_type=media_type,
            timeout_millis=timeout_millis,
            kwargs=kwargs,
        )

    @staticmethod
    def _synthesize_sync(
        *,
        text: str,
        model: str,
        voice: str,
        format_enum: "AudioFormat",
        media_type: str,
        timeout_millis: int | None,
        kwargs: dict[str, Any],
    ) -> TTSResponse:
        """Synchronously synthesize and return the full audio payload."""
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        start_datetime = datetime.now()
        synthesizer = SpeechSynthesizer(
            model=model,
            voice=voice,
            format=format_enum,
            **kwargs,
        )
        audio_data = synthesizer.call(text, timeout_millis=timeout_millis)
        elapsed = (datetime.now() - start_datetime).total_seconds()
        usage = _parse_usage(elapsed)
        metadata = _response_metadata(
            synthesizer.get_last_request_id(),
            synthesizer.get_response(),
        )
        if not audio_data:
            return TTSResponse(
                content=None,
                metadata=metadata,
                usage=usage,
            )

        return _build_audio_response(
            audio_data,
            media_type,
            metadata=metadata,
            usage=usage,
        )

    @staticmethod
    async def _parse_into_async_generator(
        text: str,
        model: str,
        voice: str,
        format_enum: "AudioFormat",
        media_type: str,
        timeout_millis: int | None,
        kwargs: dict[str, Any],
    ) -> AsyncGenerator[TTSResponse, None]:
        """Parse streaming CosyVoice results into incremental responses."""
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        callback_cls = _make_callback_class()
        callback = callback_cls()
        synthesizer = SpeechSynthesizer(
            model=model,
            voice=voice,
            format=format_enum,
            callback=callback,
            **kwargs,
        )

        start_datetime = datetime.now()
        call_task = asyncio.create_task(
            asyncio.to_thread(
                DashScopeCosyVoiceTTSModel._call_streaming_synthesizer,
                synthesizer,
                callback,
                text,
                timeout_millis,
            ),
        )

        pending: TTSResponse | None = None

        while True:
            event_type, payload = await asyncio.to_thread(callback.queue.get)
            if event_type is _StreamComplete:
                break
            if event_type is _StreamError:
                raise RuntimeError(f"CosyVoice synthesis failed: {payload}")
            if event_type is not bytes:
                continue
            if pending is not None:
                yield pending
            pending = _build_audio_response(
                payload,
                media_type,
                is_last=False,
            )

        await call_task
        elapsed = (datetime.now() - start_datetime).total_seconds()
        metadata = _response_metadata(
            synthesizer.get_last_request_id(),
            synthesizer.get_response(),
        )
        if pending is not None:
            pending.is_last = True
            pending.metadata = metadata
            pending.usage = _parse_usage(elapsed)
            yield pending
        else:
            yield TTSResponse(
                content=None,
                metadata=metadata,
                usage=_parse_usage(elapsed),
                is_last=True,
            )

    @staticmethod
    def _call_streaming_synthesizer(
        synthesizer: Any,
        callback: Any,
        text: str,
        timeout_millis: int | None,
    ) -> None:
        """Run the blocking SDK call and forward failures to the stream."""
        try:
            synthesizer.call(text, timeout_millis=timeout_millis)
        except Exception as exc:
            callback.queue.put((_StreamError, exc))
