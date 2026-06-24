# -*- coding: utf-8 -*-
"""DashScope CosyVoice Realtime TTS model implementation.

Uses the older ``dashscope.audio.tts_v2.SpeechSynthesizer`` SDK for models
such as ``cosyvoice-v3-plus``, ``cosyvoice-v3-flash``, ``sambert``, etc.
"""
import asyncio
import base64
import os
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
    from dashscope.audio.tts_v2 import ResultCallback


_MEDIA_TYPE = "audio/wav"
_SAMPLE_RATE = 24000
_CHANNELS = 1
_BITS_PER_SAMPLE = 16


def _make_cosyvoice_callback_class() -> type["ResultCallback"]:
    """Create the DashScope CosyVoice TTS callback class lazily."""
    from dashscope.audio.tts_v2 import ResultCallback

    class _CosyVoiceCallback(ResultCallback):
        """Internal callback that accumulates PCM audio from the WebSocket
        and exposes incremental deltas."""

        def __init__(self) -> None:
            """Initialize callback with audio buffer and synchronization
            events."""
            super().__init__()
            self.chunk_event = threading.Event()
            self.finish_event = threading.Event()
            self._pcm_bytes: bytearray = bytearray()
            self._consumed: int = 0

        def on_open(self) -> None:
            """Handle WebSocket open — reset audio state."""
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
                    await asyncio.to_thread(self.chunk_event.wait, 30)

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

    return _CosyVoiceCallback


class DashScopeCosyVoiceRealtimeTTSModel(TTSModelBase):
    """DashScope CosyVoice Realtime TTS model using the
    ``SpeechSynthesizer`` streaming API.

    Supports streaming input: text can be pushed incrementally via
    :meth:`push`, and :meth:`synthesize` finalizes the current utterance.

    Supported models include ``cosyvoice-v3-plus``, ``cosyvoice-v3-flash``,
    ``sambert``, etc. For more details see the `official document
    <https://help.aliyun.com/document_detail/2712523.html>`_.

    .. note:: Only one streaming input request can be active at a time.

    .. note:: Unlike ``DashScopeRealtimeTTSModel`` (Qwen3) which produces
       audio at token-level granularity, CosyVoice server automatically
       segments incoming text into sentences and synthesizes per-sentence.
       Audio is returned via callback only after a complete sentence boundary
       is detected. This means :meth:`push` may return empty responses until
       enough text accumulates to form a sentence. Calling
       :meth:`synthesize` forces synthesis of all remaining text (including
       incomplete sentences). See `official docs
       <https://help.aliyun.com/en/model-studio/cosyvoice-python-sdk>`_.
    """

    class Parameters(BaseModel):
        """Frontend-exposed parameters for CosyVoice Realtime TTS models."""

        voice: str = Field(
            default="longanyang",
            title="Voice",
            description="The voice to use for synthesis.",
        )

    type: Literal[
        "dashscope_cosyvoice_realtime_tts"
    ] = "dashscope_cosyvoice_realtime_tts"

    realtime: bool = True

    _MODELS_DIR = os.path.join(os.path.dirname(__file__), "_cosyvoice_models")

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
    ) -> list:
        """List CosyVoice model cards from the dedicated YAML directory."""
        return super().list_models(
            custom_yaml_dir=custom_yaml_dir or cls._MODELS_DIR,
        )

    def __init__(
        self,
        credential: DashScopeCredential,
        model: str = "cosyvoice-v3-plus",
        parameters: "DashScopeCosyVoiceRealtimeTTSModel.Parameters | None" = (
            None
        ),
        stream: bool = True,
        cold_start_length: int | None = None,
        cold_start_words: int | None = None,
        max_retries: int = 3,
        retry_delay: float = 5.0,
    ) -> None:
        """Initialize the DashScope CosyVoice Realtime TTS model.

        Args:
            credential (`DashScopeCredential`):
                The DashScope credential.
            model (`str`, defaults to ``"cosyvoice-v3-plus"``):
                The CosyVoice model name, e.g. ``"cosyvoice-v3-plus"``,
                ``"cosyvoice-v3-flash"``, ``"sambert"``.
            parameters (`Parameters | None`, defaults to `None`):
                The TTS parameters (voice, etc.).
            stream (`bool`, defaults to `True`):
                Whether :meth:`synthesize` returns a streaming async generator.
            cold_start_length (`int | None`, defaults to `None`):
                Minimum character count before the first text chunk is sent
                to the synthesizer.
            cold_start_words (`int | None`, defaults to `None`):
                Minimum word count (split by spaces) before the first text
                chunk is sent.
            max_retries (`int`, defaults to `3`):
                Max retry attempts on synthesis failure.
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

        self._synthesizer: Any = None
        self._callback: Any = None
        self._connected = False
        self._cold_start_buffer: str = ""
        self._cold_start_done: bool = False
        self._accumulated_text: str = ""

    def _create_synthesizer(self) -> None:
        """Create a fresh SpeechSynthesizer and callback."""
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat

        dashscope.api_key = self.credential.api_key.get_secret_value()

        callback_cls = _make_cosyvoice_callback_class()
        self._callback = callback_cls()
        self._synthesizer = SpeechSynthesizer(
            model=self.model,
            voice=self.parameters.voice,
            format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            callback=self._callback,
        )

    async def connect(self) -> None:
        """Initialize the SpeechSynthesizer."""
        if self._connected:
            return
        self._create_synthesizer()
        self._connected = True

    async def close(self) -> None:
        """Close the SpeechSynthesizer."""
        if not self._connected:
            return
        self._connected = False
        try:
            if self._synthesizer is not None:
                self._synthesizer.close()
        except Exception:
            pass

    async def _reconnect(self) -> None:
        """Reconnect by recreating the synthesizer."""
        try:
            if self._synthesizer is not None:
                self._synthesizer.close()
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

        .. note:: The CosyVoice server automatically segments text into
           sentences before synthesizing. Audio is only produced after a
           complete sentence is detected, so this method often returns an
           empty response (``content=None``) for partial sentences. Remaining
           audio is force-synthesized when :meth:`synthesize` is called.
           See `CosyVoice Python SDK docs
           <https://help.aliyun.com/en/model-studio/cosyvoice-python-sdk>`_.

        Args:
            text (`str`):
                An incremental text chunk (delta) to append.
            **kwargs (`Any`):
                Additional keyword arguments (unused).

        Returns:
            `TTSResponse`:
                Audio accumulated so far, or empty if not yet available.
        """
        if not self._connected:
            raise RuntimeError(
                "TTS model is not connected. Call `connect()` first.",
            )

        if not text:
            return TTSResponse(content=None)

        self._accumulated_text += text

        if self._cold_start_done:
            text_to_send = text
        else:
            self._cold_start_buffer += text
            if (
                self.cold_start_length
                and len(self._cold_start_buffer) < self.cold_start_length
            ) or (
                self.cold_start_words
                and len(self._cold_start_buffer.split())
                < self.cold_start_words
            ):
                return self._callback.get_audio_response(block=False)
            text_to_send = self._cold_start_buffer
            self._cold_start_buffer = ""
            self._cold_start_done = True

        try:
            self._synthesizer.streaming_call(text_to_send)
        except Exception:
            return TTSResponse(content=None)

        return self._callback.get_audio_response(block=False)

    async def synthesize(
        self,
        text: str | None = None,
        **kwargs: Any,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        """Finalize synthesis for the current utterance.

        If text was previously pushed via :meth:`push`, this flushes any
        remaining buffered text, calls ``streaming_complete()``, and waits
        for audio. If ``text`` is provided, it is appended before finalizing.

        Args:
            text (`str | None`, defaults to `None`):
                Optional additional text to append before finalizing.
            **kwargs (`Any`):
                Additional keyword arguments (unused).

        Returns:
            `TTSResponse | AsyncGenerator[TTSResponse, None]`:
                A single response when ``stream=False``, or an async generator
                of incremental chunks when ``stream=True``.
        """
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

        try:
            if not full_text and not unsent:
                if self.stream:

                    async def _empty_gen() -> AsyncGenerator[
                        TTSResponse,
                        None,
                    ]:
                        yield TTSResponse(content=None)

                    return _empty_gen()
                return TTSResponse(content=None)

            for attempt in range(self.max_retries):
                try:
                    if unsent:
                        self._synthesizer.streaming_call(unsent)

                    self._synthesizer.streaming_complete()

                    finished = await asyncio.to_thread(
                        self._callback.finish_event.wait,
                        30,
                    )

                    if not finished:
                        logger.warning(
                            "CosyVoice TTS: timed out waiting for synthesis "
                            "completion (30s)",
                        )
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(delay)
                            await self._reconnect()
                            unsent = full_text
                            delay *= 2
                            continue
                        raise RuntimeError(
                            "CosyVoice TTS synthesis timed out after 30s",
                        )

                    if full_text and not self._callback.has_audio_data():
                        if attempt < self.max_retries - 1:
                            logger.warning(
                                "CosyVoice TTS: no audio received, retrying "
                                "(%d/%d) in %.1fs...",
                                attempt + 1,
                                self.max_retries,
                                delay,
                            )
                            await asyncio.sleep(delay)
                            await self._reconnect()
                            unsent = full_text
                            delay *= 2
                            continue
                        raise RuntimeError(
                            f"CosyVoice TTS synthesis failed: no audio after "
                            f"{self.max_retries} attempts",
                        )
                    break

                except RuntimeError:
                    raise
                except Exception as e:
                    if attempt < self.max_retries - 1:
                        logger.warning(
                            "CosyVoice TTS error, retrying (%d/%d) in "
                            "%.1fs: %s",
                            attempt + 1,
                            self.max_retries,
                            delay,
                            e,
                        )
                        await asyncio.sleep(delay)
                        await self._reconnect()
                        unsent = full_text
                        delay *= 2
                    else:
                        raise

            if self.stream:
                return self._callback.get_audio_chunks()

            response = self._callback.get_audio_response(block=True)
            self._callback.reset()
            return response
        finally:
            self._reset_state()

    def _reset_state(self) -> None:
        """Reset per-utterance tracking state."""
        self._cold_start_buffer = ""
        self._cold_start_done = False
        self._accumulated_text = ""
