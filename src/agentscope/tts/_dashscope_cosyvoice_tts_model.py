# -*- coding: utf-8 -*-
"""DashScope CosyVoice TTS model implementation."""

import base64
from typing import Any, Literal, AsyncGenerator

from ._tts_base import TTSModelBase
from ._tts_response import TTSResponse
from ._utils import _get_cosyvoice_callback_class
from ..message import Msg, AudioBlock, Base64Source
from ..types import JSONSerializableObject


class DashScopeCosyVoiceTTSModel(TTSModelBase):
    """TTS implementation for DashScope CosyVoice TTS API.
    The supported models include "cosyvoice-v3-plus",
    "cosyvoice-v3-flash", "sambert" etc.

    This model does NOT support streaming text input. For streaming input,
    use `DashScopeCosyVoiceRealtimeTTSModel` instead.

    For more details, please see the `official document
    <https://help.aliyun.com/zh/model-studio/text-to-speech>`_.
    """

    supports_streaming_input: bool = False
    """Whether the model supports streaming input."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "cosyvoice-v3-plus",
        voice: Literal[
            "longanyang",
            "longanhuan",
            "longhuhu_v3",
            "longyingmu_v3",
        ]
        | str = "longanyang",
        stream: bool = False,
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the DashScope CosyVoice TTS model by
        specifying the model, voice, and other parameters.

        .. note:: More details about the parameters, such as `model_name`,
        `voice`, and `mode` can be found in the `official document
        <https://help.aliyun.com/zh/model-studio/cosyvoice-voice-list>`_.

        Args:
            api_key (`str`):
                The DashScope API key.
            model_name (`str`, defaults to "cosyvoice-v3-plus"):
                The TTS model name, e.g. "cosyvoice-v3-plus",
                "cosyvoice-v3-flash", etc.
            voice (`Literal["longanyang", "longanhuan", "longhuhu_v3", \
            "longyingmu_v3"] | str`, defaults to "longanyang".):
                The voice to use for synthesis. Refer to `official document
                <https://help.aliyun.com/zh/model-studio/cosyvoice-voice-list>`_
                for the supported voices for each model.
            stream (`bool`, defaults to `False`):
                Whether to use streaming audio output.
            client_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments to initialize the DashScope
                CosyVoice tts client.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments used in DashScope CosyVoice
                tts API generation.
        """
        super().__init__(model_name=model_name, stream=stream)

        import dashscope

        dashscope.api_key = api_key

        # Store configuration
        self.voice = voice
        self.client_kwargs = client_kwargs or {}
        self.generate_kwargs = generate_kwargs or {}

    def _create_synthesizer(self) -> tuple:
        """Create a new SpeechSynthesizer instance for each request."""
        from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat

        callback = _get_cosyvoice_callback_class()() if self.stream else None

        synthesizer = SpeechSynthesizer(
            model=self.model_name,
            voice=self.voice,
            format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            callback=callback,
            **self.client_kwargs,
            **self.generate_kwargs,
        )
        return synthesizer, callback

    async def push(self, msg: Msg, **kwargs: Any) -> TTSResponse:
        """Push a message to the TTS model and return TTS response.

        Args:
            msg (`Msg`):
                The message to be synthesized.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the TTS API call.

        Returns:
            `TTSResponse`:
                The TTSResponse object.
        """
        return TTSResponse(content=None)

    async def synthesize(
        self,
        msg: Msg | None = None,
        **kwargs: Any,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        """Synthesize text to speech and return TTS response.

        Args:
            msg (`Msg | None`, optional):
                The message to be synthesized.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the TTS API call.

        Returns:
            `TTSResponse | AsyncGenerator[TTSResponse, None]`:
                The TTSResponse object in non-streaming mode, or an async
                generator yielding TTSResponse objects in streaming mode.
        """
        if msg is None:
            return TTSResponse(content=None)

        text = msg.get_text_content()
        if not text:
            return TTSResponse(content=None)

        # Create a new synthesizer for each request to avoid connection issues
        synthesizer, callback = self._create_synthesizer()

        if self.stream:
            # Streaming output mode: use callback to get audio chunks
            synthesizer.call(text=text)
            return callback.get_audio_chunk()
        else:
            # Non-streaming mode: call directly returns audio bytes
            audio = synthesizer.call(text=text)

            if not audio:
                return TTSResponse(content=None)

            encoded_data = base64.b64encode(audio).decode()

            return TTSResponse(
                content=AudioBlock(
                    type="audio",
                    source=Base64Source(
                        type="base64",
                        data=encoded_data,
                        media_type="audio/pcm;rate=24000",
                    ),
                ),
            )
