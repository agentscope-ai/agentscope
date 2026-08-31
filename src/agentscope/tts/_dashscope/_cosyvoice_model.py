# -*- coding: utf-8 -*-
"""DashScope CosyVoice TTS model implementation."""
import asyncio
import base64
import io
import os
from typing import Any, AsyncGenerator, Literal, TYPE_CHECKING
import wave

from pydantic import BaseModel, Field

from .._tts_base import TTSModelBase
from .._tts_model_card import TTSModelCard
from .._tts_response import TTSResponse
from ._cosyvoice_utils import (
    _BITS_PER_SAMPLE,
    _CHANNELS,
    _make_cosyvoice_callback_class,
    _MEDIA_TYPE,
    _SAMPLE_RATE,
)
from ..._logging import logger
from ...credential import DashScopeCredential
from ...message import DataBlock, Base64Source

if TYPE_CHECKING:
    from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback


class DashScopeCosyVoiceTTSModel(TTSModelBase):
    """DashScope CosyVoice TTS model using the WebSocket SpeechSynthesizer."""

    class Parameters(BaseModel):
        """Frontend-exposed parameters for DashScope CosyVoice TTS models."""

        voice: str = Field(
            default="longanhuan",
            title="Voice",
            description="The voice to use for synthesis.",
        )
        realtime: bool = Field(
            default=False,
            title="Realtime",
            description="Whether to enable streaming input mode.",
        )

    type: Literal["dashscope_cosyvoice_tts"] = "dashscope_cosyvoice_tts"
    """The type of the TTS model."""

    _MODELS_DIR = os.path.join(os.path.dirname(__file__), "_cosyvoice_models")

    def __init__(
        self,
        credential: DashScopeCredential,
        model: str = "cosyvoice-v3-flash",
        parameters: "DashScopeCosyVoiceTTSModel.Parameters | None" = None,
        stream: bool = True,
        cold_start_length: int | None = None,
        cold_start_words: int | None = None,
        max_retries: int = 3,
        retry_delay: float = 5.0,
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
            cold_start_length (`int | None`, defaults to `None`):
                Minimum character count before the first realtime text chunk is
                sent to the synthesizer.
            cold_start_words (`int | None`, defaults to `None`):
                Minimum word count before the first realtime text chunk is
                sent.
            max_retries (`int`, defaults to `3`):
                Max retry attempts on realtime synthesis failure.
            retry_delay (`float`, defaults to `5.0`):
                Initial retry delay in seconds (exponential backoff).
        """
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            stream=stream,
        )
        self.realtime = self.parameters.realtime
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

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
    ) -> list[TTSModelCard]:
        """List CosyVoice model cards from the dedicated card directory."""
        return super().list_models(
            custom_yaml_dir=custom_yaml_dir or cls._MODELS_DIR,
        )

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
                Reserved for API compatibility. Runtime synthesis options
                are configured when the model is initialized.

        Returns:
            `TTSResponse | AsyncGenerator[TTSResponse, None]`:
                A single aggregated response when ``stream=False``, or an
                async generator yielding incremental chunks when
                ``stream=True``.
        """
        if self.realtime:
            return await self._synthesize_realtime(text)

        return self._synthesize_once(text)

    def _synthesize_once(
        self,
        text: str | None = None,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        """Synthesize text with the non-realtime one-shot SDK path."""
        if not text:
            return TTSResponse(content=None)

        synthesizer, callback = self._create_synthesizer(
            callback=self.stream,
        )

        if self.stream:
            assert callback is not None
            synthesizer.call(text=text)
            return callback.get_audio_chunks()

        audio_data = synthesizer.call(text=text)
        if not audio_data:
            return TTSResponse(content=None)
        return self._build_wav_response(audio_data)

    def _create_synthesizer(
        self,
        *,
        callback: bool,
    ) -> tuple["SpeechSynthesizer", "ResultCallback | None"]:
        """Create a fresh SpeechSynthesizer and optional callback."""
        import dashscope
        from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

        dashscope.api_key = self.credential.api_key.get_secret_value()

        callback_instance = None
        if callback:
            callback_cls = _make_cosyvoice_callback_class()
            callback_instance = callback_cls()

        synthesizer = SpeechSynthesizer(
            model=self.model,
            voice=self.parameters.voice,
            format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            callback=callback_instance,
        )
        return synthesizer, callback_instance

    async def connect(self) -> None:
        """Initialize the SpeechSynthesizer for realtime mode."""
        if self._connected:
            return
        self._synthesizer, self._callback = self._create_synthesizer(
            callback=True,
        )
        self._connected = True

    async def close(self) -> None:
        """Close the realtime SpeechSynthesizer."""
        if not self._connected:
            return
        self._connected = False
        try:
            if self._synthesizer is not None:
                self._synthesizer.close()
        except Exception:
            pass

    async def _reconnect(self) -> None:
        """Reconnect by recreating the realtime synthesizer."""
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
        """Push an incremental text delta for realtime synthesis."""
        del kwargs
        if not self.realtime:
            return await super().push(text)

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

    async def _synthesize_realtime(
        self,
        text: str | None = None,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        """Finalize synthesis for the current realtime utterance."""
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
        """Reset per-utterance realtime tracking state."""
        self._cold_start_buffer = ""
        self._cold_start_done = False
        self._accumulated_text = ""

    @staticmethod
    def _build_wav_response(
        audio_data: bytes,
    ) -> TTSResponse:
        """Build a self-contained WAV response from PCM audio bytes."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(_CHANNELS)
            wav.setsampwidth(_BITS_PER_SAMPLE // 8)
            wav.setframerate(_SAMPLE_RATE)
            wav.writeframes(audio_data)

        return TTSResponse(
            content=DataBlock(
                source=Base64Source(
                    data=base64.b64encode(buf.getvalue()).decode("ascii"),
                    media_type=_MEDIA_TYPE,
                ),
            ),
        )
