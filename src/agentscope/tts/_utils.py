# -*- coding: utf-8 -*-
"""Utility classes for DashScope CosyVoice TTS models."""

import base64
import threading
from typing import TYPE_CHECKING, AsyncGenerator, Any

from ._tts_response import TTSResponse
from .._logging import logger
from ..message import AudioBlock, Base64Source

if TYPE_CHECKING:
    from dashscope.audio.tts_v2 import ResultCallback
else:
    ResultCallback = "dashscope.audio.tts_v2.ResultCallback"


def _get_cosyvoice_callback_class() -> type["ResultCallback"]:
    """Get the callback class for CosyVoice TTS streaming audio output.

    This callback handles audio data accumulation with proper PCM and base64
    alignment. It encodes audio in chunks of 6 bytes (LCM of 2 and 3) to
    ensure both PCM alignment (2 bytes per sample) and base64 alignment
    (3 bytes per encoded unit).

    Returns:
        The callback class for CosyVoice TTS.
    """
    from dashscope.audio.tts_v2 import ResultCallback

    class _CosyVoiceTTSCallback(ResultCallback):
        """CosyVoice TTS callback for streaming audio output."""

        def __init__(self) -> None:
            """Initialize the CosyVoice TTS callback."""
            super().__init__()

            # The event that will be set when a new audio chunk is received
            self.chunk_event = threading.Event()
            # The event that will be set when the TTS synthesis is finished
            self.finish_event = threading.Event()
            # Accumulate raw bytes
            self._audio_bytes: bytes = b""
            # Accumulated base64 string (boundary-aligned)
            self._audio_base64: str = ""
            # Last encoded byte position (must be multiple of 6 for alignment)
            self._last_encoded_pos: int = 0

        def on_open(self) -> None:
            """Called when the WebSocket connection is opened."""
            self._audio_bytes = b""
            self._audio_base64 = ""
            self._last_encoded_pos = 0
            self.finish_event.clear()

        def on_data(self, data: bytes) -> None:
            """Called when data is received from the WebSocket connection.

            Args:
                data (`bytes`):
                    The data received from the WebSocket connection.
            """
            if data:
                self._audio_bytes += data
                # Encode in chunks of 6 bytes (LCM of 2 and 3)
                # This ensures both PCM alignment (2 bytes) and
                # base64 alignment (3 bytes)
                aligned_len = (len(self._audio_bytes) // 6) * 6
                if aligned_len > self._last_encoded_pos:
                    new_chunk = self._audio_bytes[
                        self._last_encoded_pos : aligned_len
                    ]
                    self._audio_base64 += base64.b64encode(new_chunk).decode()
                    self._last_encoded_pos = aligned_len

                # Signal that a new audio chunk is available
                if not self.chunk_event.is_set():
                    self.chunk_event.set()

        def on_close(self) -> None:
            """Called when the WebSocket connection is closed."""
            # Encode any remaining bytes
            if len(self._audio_bytes) > self._last_encoded_pos:
                remaining = self._audio_bytes[self._last_encoded_pos :]
                self._audio_base64 += base64.b64encode(remaining).decode()
                self._last_encoded_pos = len(self._audio_bytes)

            # Unblock waiting operations to prevent deadlock
            self.finish_event.set()
            self.chunk_event.set()

        def on_error(self, message: Any) -> None:
            """Called when an error occurs."""
            logger.error(message)

            # Unblock waiting operations to prevent deadlock
            self.finish_event.set()
            self.chunk_event.set()

        async def get_audio_data(self, block: bool = True) -> TTSResponse:
            """Get the current accumulated audio data as base64 string.

            Args:
                block (`bool`, defaults to `True`):
                    Whether to block until synthesis is finished.

            Returns:
                `TTSResponse`:
                    The TTSResponse containing base64-encoded audio data.
            """
            # Block until synthesis is finished
            if block:
                self.finish_event.wait()

            # Return the accumulated audio data
            if self._audio_base64:
                return TTSResponse(
                    content=AudioBlock(
                        type="audio",
                        source=Base64Source(
                            type="base64",
                            data=self._audio_base64,
                            media_type="audio/pcm;rate=24000",
                        ),
                    ),
                )

            # Reset for next tts request
            await self._reset()

            # Return empty response if no audio data
            return TTSResponse(content=None)

        async def get_audio_chunk(self) -> AsyncGenerator[TTSResponse, None]:
            """Get the audio data chunk as an async generator of TTSResponse.

            Returns accumulated base64 string. The agent code uses string
            slicing to get the delta, so we must ensure boundary alignment.

            Returns:
                `AsyncGenerator[TTSResponse, None]`:
                    The async generator yielding TTSResponse with audio chunks.
            """
            while True:
                if self.finish_event.is_set():
                    # Yield final chunk with all accumulated data
                    yield TTSResponse(
                        content=AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                data=self._audio_base64,
                                media_type="audio/pcm;rate=24000",
                            ),
                        ),
                        is_last=True,
                    )

                    # Reset for next tts request
                    await self._reset()

                    break

                if self.chunk_event.is_set():
                    # Clear the event for next chunk
                    self.chunk_event.clear()
                else:
                    # Wait for the next chunk
                    self.chunk_event.wait()

                # Yield current accumulated data
                if self._audio_base64:
                    yield TTSResponse(
                        content=AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                data=self._audio_base64,
                                media_type="audio/pcm;rate=24000",
                            ),
                        ),
                        is_last=False,
                    )

        async def _reset(self) -> None:
            """Reset the callback state for a new TTS request."""
            self.finish_event.clear()
            self.chunk_event.clear()
            self._audio_bytes = b""
            self._audio_base64 = ""
            self._last_encoded_pos = 0

        def has_audio_data(self) -> bool:
            """Check if audio data has been received."""
            return bool(self._audio_bytes)

    return _CosyVoiceTTSCallback
