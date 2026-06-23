# -*- coding: utf-8 -*-
"""DashScope CosyVoice TTS model implementation."""
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

from .._tts_base import TTSModelBase
from .._tts_model_card import TTSModelCard
from .._tts_response import TTSResponse
from ._cosyvoice_utils import (
    _audio_format_enum,
    _build_audio_response,
    _make_cosyvoice_callback_class,
    _media_type,
    _parse_usage,
    _response_metadata,
)
from ...credential import DashScopeCredential

if TYPE_CHECKING:
    from dashscope.audio.tts_v2 import AudioFormat


_DEFAULT_AUDIO_FORMAT = "wav"
_DEFAULT_SAMPLE_RATE = 24000
_DEFAULT_TIMEOUT_MILLIS = 600000


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
            from dashscope.audio.tts_v2 import SpeechSynthesizer

            callback_cls = _make_cosyvoice_callback_class(
                media_type=media_type,
                prepend_header=False,
            )
            callback = callback_cls()
            synthesizer = SpeechSynthesizer(
                model=self.model,
                voice=voice,
                format=format_enum,
                callback=callback,
                **kwargs,
            )
            start_datetime = datetime.now()
            synthesizer.streaming_call(text)
            synthesizer.streaming_complete()
            return self._stream_audio_chunks(
                synthesizer=synthesizer,
                callback=callback,
                start_datetime=start_datetime,
            )

        return self._synthesize_sync(
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
    async def _stream_audio_chunks(
        *,
        synthesizer: Any,
        callback: Any,
        start_datetime: datetime,
    ) -> AsyncGenerator[TTSResponse, None]:
        """Stream callback audio chunks and attach final response metadata."""
        pending: TTSResponse | None = None
        async for chunk in callback.get_audio_chunks():
            if chunk.content is None and chunk.is_last and pending is not None:
                break
            if pending is not None:
                yield pending
            pending = chunk

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
