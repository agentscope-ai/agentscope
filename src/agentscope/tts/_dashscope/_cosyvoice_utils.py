# -*- coding: utf-8 -*-
"""Shared helpers for DashScope CosyVoice TTS models."""
import asyncio
import base64
import threading
from typing import Any, AsyncGenerator, TYPE_CHECKING

from .._tts_response import TTSResponse
from ..._logging import logger
from ..._utils._audio import _build_streaming_wav_header
from ...message import DataBlock, Base64Source

if TYPE_CHECKING:
    from dashscope.audio.tts_v2 import ResultCallback


_SAMPLE_RATE = 24000
_CHANNELS = 1
_BITS_PER_SAMPLE = 16
_MEDIA_TYPE = "audio/wav"


def _build_audio_response(
    audio_data: bytes,
    media_type: str,
    *,
    is_last: bool = True,
) -> TTSResponse:
    """Build a TTSResponse containing base64-encoded audio data."""
    return TTSResponse(
        content=DataBlock(
            source=Base64Source(
                data=base64.b64encode(audio_data).decode("ascii"),
                media_type=media_type,
            ),
        ),
        is_last=is_last,
    )


def _make_cosyvoice_callback_class() -> type["ResultCallback"]:
    """Create the shared CosyVoice callback class lazily."""
    from dashscope.audio.tts_v2 import ResultCallback

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
            if header:
                return _build_streaming_wav_header(
                    sample_rate=_SAMPLE_RATE,
                    channels=_CHANNELS,
                    bits_per_sample=_BITS_PER_SAMPLE,
                ) + bytes(new_data)
            return bytes(new_data)

        def get_audio_response(self, block: bool) -> TTSResponse:
            """Return incremental audio delta."""
            if block:
                self.finish_event.wait()
            delta = self._take_delta(header=self._consumed == 0)
            if delta:
                return _build_audio_response(delta, _MEDIA_TYPE)
            return TTSResponse(content=None)

        async def get_audio_chunks(
            self,
        ) -> AsyncGenerator[TTSResponse, None]:
            """Yield incremental audio chunks as they arrive."""
            header_sent = self._consumed > 0
            while True:
                if self.finish_event.is_set():
                    delta = self._take_delta(header=not header_sent)
                    if delta:
                        yield _build_audio_response(
                            delta,
                            _MEDIA_TYPE,
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
                        _MEDIA_TYPE,
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
