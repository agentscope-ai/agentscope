# -*- coding: utf-8 -*-
"""DashScope CosyVoice TTS model implementation."""
import base64
import io
from pathlib import Path
from typing import Any, AsyncGenerator, Literal, TYPE_CHECKING
import wave

from pydantic import BaseModel, Field

from .._tts_base import TTSModelBase
from .._tts_model_card import TTSModelCard
from .._tts_response import TTSResponse
from ._cosyvoice_utils import (
    _make_cosyvoice_callback_class,
)
from ...credential import DashScopeCredential
from ...message import DataBlock, Base64Source
from ...types import JSONSerializableObject

if TYPE_CHECKING:
    from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback


_SAMPLE_RATE = 24000
_CHANNELS = 1
_BITS_PER_SAMPLE = 16
_MEDIA_TYPE = "audio/wav"


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
            custom_yaml_dir = str(
                Path(__file__).parent / "_cosyvoice_models",
            )
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

        Returns:
            `TTSResponse | AsyncGenerator[TTSResponse, None]`:
                A single aggregated response when ``stream=False``, or an
                async generator yielding incremental chunks when
                ``stream=True``.
        """
        if not text:
            return TTSResponse(content=None)

        synthesizer, callback = self._create_synthesizer(
            callback=self.stream,
        )

        if self.stream:
            assert callback is not None
            synthesizer.call(text=text, **kwargs)
            return callback.get_audio_chunks()

        audio_data = synthesizer.call(text=text, **kwargs)
        metadata = self._response_metadata(synthesizer)
        if not audio_data:
            return TTSResponse(content=None, metadata=metadata)
        return self._build_wav_response(
            audio_data,
            metadata=metadata,
        )

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

    @staticmethod
    def _response_metadata(
        synthesizer: "SpeechSynthesizer",
    ) -> dict[str, JSONSerializableObject] | None:
        """Build metadata from the WebSocket SDK response."""
        metadata: dict[str, JSONSerializableObject] = {}
        request_id = synthesizer.get_last_request_id()
        response = synthesizer.get_response()
        if request_id is not None:
            metadata["request_id"] = request_id
        if response is not None:
            metadata["response"] = response
        return metadata or None

    @staticmethod
    def _build_wav_response(
        audio_data: bytes,
        *,
        metadata: dict[str, JSONSerializableObject] | None = None,
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
            metadata=metadata,
        )
