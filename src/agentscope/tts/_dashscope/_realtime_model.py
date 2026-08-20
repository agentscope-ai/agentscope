# -*- coding: utf-8 -*-
"""DashScope Realtime TTS model implementation."""
import asyncio
import base64
import threading
from typing import Any, AsyncGenerator, Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

from .._tts_base import TTSModelBase
from .._tts_response import TTSResponse
from ..._logging import logger
from ..._utils._audio import _build_streaming_wav_header
from ...credential import DashScopeCredential
from ...message import DataBlock, Base64Source

if TYPE_CHECKING:
    from dashscope.audio.qwen_tts_realtime import (
        QwenTtsRealtime,
        QwenTtsRealtimeCallback,
    )


_MEDIA_TYPE = "audio/wav"
_SAMPLE_RATE = 24000
_CHANNELS = 1
_BITS_PER_SAMPLE = 16


def _make_callback_class() -> type["QwenTtsRealtimeCallback"]:
    """Create the DashScope realtime TTS callback class lazily to avoid
    importing dashscope at module level."""
    from dashscope.audio.qwen_tts_realtime import QwenTtsRealtimeCallback

    class _Callback(QwenTtsRealtimeCallback):
        """Internal callback that accumulates PCM audio from the WebSocket.

        Audio data is stored as raw bytes (decoded from base64) to allow
        incremental delta extraction without base64 boundary issues.
        """

        def __init__(self) -> None:
            """Initialize callback with audio buffer and synchronization
            events."""
            super().__init__()
            self.chunk_event = threading.Event()
            self.finish_event = threading.Event()
            self._pcm_bytes: bytearray = bytearray()
            self._consumed: int = 0

        def on_event(self, response: dict[str, Any]) -> None:
            """Handle incoming WebSocket events from the TTS service."""
            try:
                event_type = response.get("type")

                if event_type == "session.created":
                    self._pcm_bytes = bytearray()
                    self._consumed = 0
                    self.finish_event.clear()
                    self.chunk_event.clear()

                elif event_type == "response.audio.delta":
                    audio_data = response.get("delta")
                    if audio_data:
                        if isinstance(audio_data, bytes):
                            self._pcm_bytes += audio_data
                        else:
                            self._pcm_bytes += base64.b64decode(audio_data)
                        if not self.chunk_event.is_set():
                            self.chunk_event.set()

                elif event_type == "session.finished":
                    self.chunk_event.set()
                    self.finish_event.set()

            except Exception:
                logger.exception("Error in TTS WebSocket callback")
                self.finish_event.set()

        def on_close(self, close_status_code: int, close_msg: str) -> None:
            """Handle WebSocket connection closure."""
            self.finish_event.set()
            self.chunk_event.set()
            if close_status_code:
                logger.warning(
                    "TTS WebSocket closed with code %s: %s",
                    close_status_code,
                    close_msg,
                )

        def _take_delta(self, header: bool = False) -> bytes | None:
            """Return new PCM bytes since last call, or None if empty.

            Args:
                header: If True, prepend a streaming WAV header to the
                    first returned chunk.
            """
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
            """Return incremental audio delta (non-blocking or blocking)."""
            if block:
                self.finish_event.wait()
            delta = self._take_delta(header=self._consumed == 0)
            if delta:
                return TTSResponse(
                    content=DataBlock(
                        source=Base64Source(
                            data=base64.b64encode(delta).decode("ascii"),
                            media_type=_MEDIA_TYPE,
                        ),
                    ),
                )
            return TTSResponse(content=None)

        async def get_audio_chunks(self) -> AsyncGenerator[TTSResponse, None]:
            """Yield incremental WAV audio chunks as they arrive."""
            header_sent = self._consumed > 0
            while True:
                if self.finish_event.is_set():
                    delta = self._take_delta(header=not header_sent)
                    if delta:
                        yield TTSResponse(
                            content=DataBlock(
                                source=Base64Source(
                                    data=base64.b64encode(delta).decode(
                                        "ascii",
                                    ),
                                    media_type=_MEDIA_TYPE,
                                ),
                            ),
                            is_last=True,
                        )
                    else:
                        yield TTSResponse(content=None, is_last=True)
                    self.reset()
                    break

                if self.chunk_event.is_set():
                    self.chunk_event.clear()
                else:
                    await asyncio.to_thread(self.chunk_event.wait)

                if self.finish_event.is_set():
                    continue

                delta = self._take_delta(header=not header_sent)
                if delta:
                    header_sent = True
                    yield TTSResponse(
                        content=DataBlock(
                            source=Base64Source(
                                data=base64.b64encode(delta).decode("ascii"),
                                media_type=_MEDIA_TYPE,
                            ),
                        ),
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

    return _Callback


class DashScopeRealtimeTTSModel(TTSModelBase):
    """DashScope Realtime TTS model using the QwenTtsRealtime WebSocket API.

    This model supports streaming input: text can be pushed incrementally
    via :meth:`push`, and :meth:`synthesize` finalizes the current utterance.

    For more details see the `official document
    <https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2938790>`_.

    .. note:: Only one streaming input request can be active at a time.
    """

    class Parameters(BaseModel):
        """Frontend-exposed parameters for DashScope Realtime TTS models."""

        voice: str = Field(
            default="Cherry",
            title="Voice",
            description="The voice to use for synthesis.",
        )

    type: Literal["dashscope_realtime_tts"] = "dashscope_realtime_tts"

    realtime: bool = True

    def __init__(
        self,
        credential: DashScopeCredential,
        model: str = "qwen3-tts-flash-realtime",
        parameters: "DashScopeRealtimeTTSModel.Parameters | None" = None,
        stream: bool = True,
        cold_start_length: int | None = None,
        cold_start_words: int | None = None,
        max_retries: int = 3,
        retry_delay: float = 5.0,
    ) -> None:
        """Initialize the DashScope Realtime TTS model.

        Args:
            credential (`DashScopeCredential`):
                The DashScope credential.
            model (`str`, defaults to ``"qwen3-tts-flash-realtime"``):
                The realtime TTS model name.
            parameters (`Parameters | None`, defaults to `None`):
                The TTS parameters (voice, etc.).
            stream (`bool`, defaults to `True`):
                Whether :meth:`synthesize` returns a streaming async generator.
            cold_start_length (`int | None`, defaults to `None`):
                Minimum character count before the first text chunk is sent.
            cold_start_words (`int | None`, defaults to `None`):
                Minimum word count before the first text chunk is sent.
            max_retries (`int`, defaults to `3`):
                Max retry attempts on WebSocket failure.
            retry_delay (`float`, defaults to `5.0`):
                Initial retry delay in seconds (exponential backoff).
        """
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            stream=stream,
        )
        self.cold_start_length = cold_start_length
        self.cold_start_words = cold_start_words
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        self._tts_client: QwenTtsRealtime | None = None
        self._callback: Any = None
        self._connected = False
        self._cold_start_buffer: str = ""
        self._cold_start_done: bool = False
        self._accumulated_text: str = ""

    def _create_client(self) -> None:
        """Create a fresh TTS client and callback."""
        import dashscope
        from dashscope.audio.qwen_tts_realtime import QwenTtsRealtime

        dashscope.api_key = self.credential.api_key.get_secret_value()

        callback_cls = _make_callback_class()
        self._callback = callback_cls()
        self._tts_client = QwenTtsRealtime(
            model=self.model,
            callback=self._callback,
        )

    async def connect(self) -> None:
        """Establish the WebSocket connection."""
        if self._connected:
            return

        self._create_client()
        self._tts_client.connect()
        self._tts_client.update_session(
            voice=self.parameters.voice,
            mode="server_commit",
        )
        self._connected = True

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if not self._connected:
            return
        self._connected = False
        try:
            self._tts_client.close()
        except Exception:
            pass

    async def _reconnect(self) -> None:
        """Reconnect by recreating the client."""
        try:
            self._tts_client.close()
        except Exception:
            pass
        self._connected = False
        self._cold_start_buffer = ""
        self._cold_start_done = False
        await self.connect()

    async def push(
        self,
        text: str,
        **kwargs: Any,
    ) -> TTSResponse:
        """Push an incremental text delta for realtime synthesis.

        Args:
            text (`str`):
                An incremental text chunk (delta) to append.
            **kwargs (`Any`):
                Additional keyword arguments (unused).

        Returns:
            `TTSResponse`:
                Audio accumulated so far, or empty if not yet available.
        """
        from websocket import WebSocketConnectionClosedException

        if not self._connected:
            raise RuntimeError(
                "TTS model is not connected. Call `connect()` first.",
            )

        if not text:
            return TTSResponse(content=None)

        self._accumulated_text += text

        if not self._cold_start_done:
            self._cold_start_buffer += text
            ready = True
            if (
                self.cold_start_length
                and len(self._cold_start_buffer) < self.cold_start_length
            ):
                ready = False
            if (
                ready
                and self.cold_start_words
                and len(self._cold_start_buffer.split())
                < self.cold_start_words
            ):
                ready = False
            if ready:
                try:
                    self._tts_client.append_text(self._cold_start_buffer)
                except WebSocketConnectionClosedException:
                    return TTSResponse(content=None)
                self._cold_start_buffer = ""
                self._cold_start_done = True
        else:
            try:
                self._tts_client.append_text(text)
            except WebSocketConnectionClosedException:
                return TTSResponse(content=None)

        return self._callback.get_audio_response(block=False)

    async def synthesize(
        self,
        text: str | None = None,
        **kwargs: Any,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        """Finalize synthesis for the current utterance.

        If text was previously pushed via :meth:`push`, this flushes any
        remaining buffered text, commits, and waits for audio. If ``text``
        is provided, it is appended before committing.

        Args:
            text (`str | None`, defaults to `None`):
                Optional additional text to append before finalizing.
                If ``None``, finalizes previously pushed text.
            **kwargs (`Any`):
                Additional keyword arguments (unused).

        Returns:
            `TTSResponse | AsyncGenerator[TTSResponse, None]`:
                A single response when ``stream=False``, or an async generator
                of incremental chunks when ``stream=True``.
        """
        from websocket import WebSocketConnectionClosedException

        if not self._connected:
            raise RuntimeError(
                "TTS model is not connected. Call `connect()` first.",
            )

        if text is not None:
            self._accumulated_text += text

        unsent = self._cold_start_buffer
        if text is not None:
            unsent += text
        self._cold_start_buffer = ""

        full_text = self._accumulated_text
        delay = self.retry_delay

        for attempt in range(self.max_retries):
            try:
                if unsent:
                    self._tts_client.append_text(unsent)

                self._tts_client.commit()
                self._tts_client.finish()

                await asyncio.to_thread(
                    self._callback.finish_event.wait,
                )

                if full_text and not self._callback.has_audio_data():
                    if attempt < self.max_retries - 1:
                        logger.warning(
                            "TTS: no audio received, retrying (%d/%d) in "
                            "%.1fs...",
                            attempt + 1,
                            self.max_retries,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        await self._reconnect()
                        unsent = full_text
                        delay *= 2
                        continue
                    self._reset_state()
                    raise RuntimeError(
                        f"TTS synthesis failed: no audio after "
                        f"{self.max_retries} attempts",
                    )
                break

            except WebSocketConnectionClosedException:
                if attempt < self.max_retries - 1:
                    logger.warning(
                        "TTS WebSocket closed, retrying (%d/%d) in %.1fs...",
                        attempt + 1,
                        self.max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    await self._reconnect()
                    unsent = full_text
                    delay *= 2
                else:
                    self._reset_state()
                    raise

        self._reset_state()

        if self.stream:
            return self._callback.get_audio_chunks()

        response = self._callback.get_audio_response(block=True)
        self._callback.reset()
        return response

    def _reset_state(self) -> None:
        """Reset per-utterance tracking state."""
        self._cold_start_buffer = ""
        self._cold_start_done = False
        self._accumulated_text = ""
