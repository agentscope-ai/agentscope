# -*- coding: utf-8 -*-
"""Gemini TTS model implementation."""
import base64
from typing import TYPE_CHECKING, Any, Literal, AsyncGenerator, Iterator

from ._tts_base import TTSModelBase
from ._tts_response import TTSResponse
from ..message import Msg, AudioBlock, Base64Source
from ..types import JSONSerializableObject

if TYPE_CHECKING:
    from google.genai import Client
    from google.genai.types import GenerateContentResponse
else:
    Client = "google.genai.Client"
    GenerateContentResponse = "google.genai.types.GenerateContentResponse"


class GeminiTTSModel(TTSModelBase):
    """Gemini TTS model implementation.
    For more details, please see the `official document
    <https://ai.google.dev/gemini-api/docs/speech-generation>`_.
    """

    supports_streaming_input: bool = False
    """Whether the model supports streaming input."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.5-flash-preview-tts",
        voice: Literal["Zephyr", "Kore", "Orus", "Autonoe"] | str = "Kore",
        stream: bool = True,
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the Gemini TTS model.

        .. note::
            More details about the parameters, such as `model_name` and
            `voice` can be found in the `official document
            <https://ai.google.dev/gemini-api/docs/speech-generation>`_.

        Args:
            api_key (`str`):
                The Gemini API key.
            model_name (`str`, defaults to "gemini-2.5-flash-preview-tts"):
                The TTS model name. Supported models are
                "gemini-2.5-flash-preview-tts",
                "gemini-2.5-pro-preview-tts", etc.
            voice (`Literal["Zephyr", "Kore", "Orus", "Autonoe"] | str`, \
             defaults to "Kore"):
                The voice name to use. Supported voices are "Zephyr",
                "Kore", "Orus", "Autonoe", etc.
            stream (`bool`, defaults to `True`):
                Whether to use streaming synthesis if supported by the model.
            client_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments to initialize the Gemini client.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
               The extra keyword arguments used in Gemini API generation,
               e.g. `temperature`, `seed`.
        """
        super().__init__(model_name=model_name, stream=stream)

        self.api_key = api_key
        self.voice = voice

        from google import genai

        self._client = genai.Client(
            api_key=self.api_key,
            **(client_kwargs or {}),
        )

        self.generate_kwargs = generate_kwargs or {}

    async def synthesize(
        self,
        msg: Msg | None = None,
        **kwargs: Any,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        """Append text to be synthesized and return TTS response.

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

        from google.genai import types

        # Only call API for synthesis when last=True
        text = msg.get_text_content()

        # Prepare config
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice,
                    ),
                ),
            ),
            **self.generate_kwargs,
            **kwargs,
        )

        # Prepare API kwargs
        api_kwargs: dict[str, JSONSerializableObject] = {
            "model": self.model_name,
            "contents": text,
            "config": config,
        }

        if self.stream:
            response = self._client.models.generate_content_stream(
                **api_kwargs,
            )
            return self._parse_into_async_generator(response)

        # Call Gemini TTS API
        response = self._client.models.generate_content(**api_kwargs)

        # Extract audio data
        if (
            response.candidates
            and response.candidates[0].content
            and response.candidates[0].content.parts
            and response.candidates[0].content.parts[0].inline_data
        ):
            audio_data = (
                response.candidates[0].content.parts[0].inline_data.data
            )
            mime_type = (
                response.candidates[0].content.parts[0].inline_data.mime_type
            )
            # Convert PCM data to base64
            audio_base64 = base64.b64encode(audio_data).decode("utf-8")

            audio_block = AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data=audio_base64,
                    media_type=mime_type,
                ),
            )
            return TTSResponse(content=audio_block)

        else:
            # Not the last chunk, return empty AudioBlock
            return TTSResponse(
                content=AudioBlock(
                    type="audio",
                    source=Base64Source(
                        type="base64",
                        data="",
                        media_type="audio/pcm;rate=24000",
                    ),
                ),
            )

    @staticmethod
    async def _parse_into_async_generator(
        response: Iterator[GenerateContentResponse],
    ) -> AsyncGenerator[TTSResponse, None]:
        """Parse the TTS response into an async generator.

        Args:
            response (`Iterator[GenerateContentResponse]`):
                The streaming response from Gemini TTS API.

        Returns:
            `AsyncGenerator[TTSResponse, None]`:
                An async generator yielding TTSResponse objects.
        """
        audio_data = ""
        for chunk in response:
            chunk_audio_data = (
                chunk.candidates[0].content.parts[0].inline_data.data
            )
            mime_type = (
                chunk.candidates[0].content.parts[0].inline_data.mime_type
            )
            chunk_audio_base64 = base64.b64encode(chunk_audio_data).decode(
                "utf-8",
            )
            audio_data += chunk_audio_base64
            yield TTSResponse(
                content=AudioBlock(
                    type="audio",
                    source=Base64Source(
                        type="base64",
                        data=audio_data,
                        media_type=mime_type,
                    ),
                ),
            )
        yield TTSResponse(content=None)
