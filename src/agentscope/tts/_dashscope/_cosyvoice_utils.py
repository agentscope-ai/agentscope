# -*- coding: utf-8 -*-
"""Shared helpers for DashScope CosyVoice TTS models."""
import asyncio
import base64
import threading
from typing import Any, AsyncGenerator, TYPE_CHECKING

from .._tts_response import TTSResponse, TTSUsage
from ..._logging import logger
from ..._utils._audio import _build_streaming_wav_header
from ...message import DataBlock, Base64Source
from ...types import JSONSerializableObject

if TYPE_CHECKING:
    from dashscope.audio.tts_v2 import AudioFormat, ResultCallback


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


def _make_cosyvoice_callback_class(
    *,
    sample_rate: int = 24000,
    channels: int = 1,
    bits_per_sample: int = 16,
    media_type: str | None = None,
    prepend_header: bool = True,
) -> type["ResultCallback"]:
    """Create the shared CosyVoice callback class lazily."""
    from dashscope.audio.tts_v2 import ResultCallback

    response_media_type = media_type or _media_type("wav", sample_rate)

    class _CosyVoiceCallback(ResultCallback):
        """Accumulate PCM audio from the WebSocket and expose deltas."""

        def __init__(self) -> None:
            """Initialize callback with audio buffer and synchronization
            events."""
            super().__init__()
            self.chunk_event = threading.Event()
            self.finish_event = threading.Event()
            self._pcm_bytes: bytearray = bytearray()
            self._consumed: int = 0

        def on_open(self) -> None:
            """Handle WebSocket open by resetting audio state."""
            self._pcm_bytes = bytearray()
            self._consumed = 0
            self.finish_event.clear()
            self.chunk_event.clear()

        def on_data(self, data: bytes) -> None:
            """Handle incoming PCM audio data."""
            if data:
                self._pcm_bytes += data
                if not self.chunk_event.is_set():
                    self.chunk_event.set()

        def on_complete(self) -> None:
            """Handle synthesis completion."""
            self.finish_event.set()
            self.chunk_event.set()

        def on_close(self) -> None:
            """Handle WebSocket close."""
            self.finish_event.set()
            self.chunk_event.set()

        def on_error(self, message: Any) -> None:
            """Handle synthesis error."""
            logger.error("CosyVoice TTS error: %s", message)
            self.finish_event.set()
            self.chunk_event.set()

        def _take_delta(self, header: bool = False) -> bytes | None:
            """Return new PCM bytes since last call, or None if empty."""
            new_data = self._pcm_bytes[self._consumed :]
            if not new_data:
                return None
            self._consumed = len(self._pcm_bytes)
            if header and prepend_header:
                return _build_streaming_wav_header(
                    sample_rate=sample_rate,
                    channels=channels,
                    bits_per_sample=bits_per_sample,
                ) + bytes(new_data)
            return bytes(new_data)

        def get_audio_response(self, block: bool) -> TTSResponse:
            """Return incremental audio delta."""
            if block:
                self.finish_event.wait()
            delta = self._take_delta(header=self._consumed == 0)
            if delta:
                return _build_audio_response(delta, response_media_type)
            return TTSResponse(content=None)

        async def get_audio_chunks(
            self,
        ) -> AsyncGenerator[TTSResponse, None]:
            """Yield incremental audio chunks as they arrive."""
            header_sent = self._consumed > 0 or not prepend_header
            while True:
                if self.finish_event.is_set():
                    delta = self._take_delta(header=not header_sent)
                    if delta:
                        yield _build_audio_response(
                            delta,
                            response_media_type,
                            is_last=True,
                        )
                    else:
                        yield TTSResponse(content=None, is_last=True)
                    self.reset()
                    break

                if self.chunk_event.is_set():
                    self.chunk_event.clear()
                else:
                    await asyncio.to_thread(self.chunk_event.wait, 30)

                if self.finish_event.is_set():
                    continue

                delta = self._take_delta(header=not header_sent)
                if delta:
                    header_sent = True
                    yield _build_audio_response(
                        delta,
                        response_media_type,
                        is_last=False,
                    )

        def reset(self) -> None:
            """Reset internal state for the next utterance."""
            self.finish_event.clear()
            self.chunk_event.clear()
            self._pcm_bytes = bytearray()
            self._consumed = 0

        def has_audio_data(self) -> bool:
            """Return whether any audio data has been received."""
            return bool(self._pcm_bytes)

    return _CosyVoiceCallback


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
