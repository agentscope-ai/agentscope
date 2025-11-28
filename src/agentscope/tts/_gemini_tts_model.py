# -*- coding: utf-8 -*-
"""Gemini TTS model implementation."""
import base64
from typing import TYPE_CHECKING, Any, Literal

from ._tts_base import TTSModelBase
from ._tts_response import TTSResponse
from ..message import Msg, AudioBlock, Base64Source
from ..types import JSONSerializableObject

if TYPE_CHECKING:
    from google.genai import Client
else:
    Client = "google.genai.Client"


class GeminiTTSModel(TTSModelBase):
    """Gemini TTS model implementation.
    For more details, please see the `official document
    <https://ai.google.dev/gemini-api/docs/speech-generation>`_.
    """

    # This model does not support streaming input (requires complete text)
    supports_streaming_input: bool = False

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.5-flash-preview-tts",
        voice: Literal["Zephyr", "Kore", "Orus", "Autonoe"] | str = "Kore",
        client_kwargs: dict | None = None,
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
            voice (`Literal["Zephyr", "Kore", "Orus", "Autonoe"] | str`,
             defaults to "Kore"):
                The voice name to use. Supported voices are "Zephyr",
                "Kore", "Orus", "Autonoe", etc.
            client_kwargs (`dict | None`, default `None`):
                The extra keyword arguments to initialize the Gemini client.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
               The extra keyword arguments used in Gemini API generation,
               e.g. `temperature`, `seed`.
        """
        super().__init__(model_name=model_name, stream=False)

        self.api_key = api_key
        self.voice = voice
        self._client: Client | None = None
        # Text buffer for each message to accumulate text before synthesis
        # Key is msg.id, value is the accumulated text
        self._text_buffer: dict[str, str] = {}
        self.client_kwargs = client_kwargs or {}
        self.generate_kwargs = generate_kwargs or {}

    async def initialize(self) -> None:
        """Initialize the Gemini TTS model and create client."""
        if self._client is not None:
            return
        from google import genai

        # Create client (API key is set via environment variable)
        self._client = genai.Client(
            api_key=self.api_key,
            **self.client_kwargs,
        )

    async def _call_api(
        self,
        msg: Msg,
        last: bool = False,
        **kwargs: Any,
    ) -> TTSResponse:
        """Append text to be synthesized and return TTS response.

        Args:
            msg (`Msg`):
                The message to be synthesized.
            last (`bool`):
                Whether this is the last chunk. Defaults to False.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the TTS API call.

        Returns:
            `TTSResponse`:
                The TTSResponse containing audio blocks.
        """
        from google.genai import types

        # Auto-initialize if not already initialized (lazy loading)
        if self._client is None:
            await self.initialize()

        msg_id = msg.id
        # Initialize text buffer for this message if not exists
        if msg_id not in self._text_buffer:
            self._text_buffer[msg_id] = ""

        # Extract text content
        for block in msg.get_content_blocks():
            if block["type"] == "text":
                text = block["text"]
                self._text_buffer[msg_id] = text

        # Only call API for synthesis when last=True
        if last and self._text_buffer.get(msg_id):
            try:
                # Prepare config
                config = types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(  # noqa
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
                    "contents": self._text_buffer[msg_id],
                    "config": config,
                }

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
                        response.candidates[0]
                        .content.parts[0]
                        .inline_data.data
                    )
                    # Convert PCM data to base64
                    audio_base64 = base64.b64encode(audio_data).decode("utf-8")

                    # Clear text buffer for this message
                    del self._text_buffer[msg_id]

                    audio_block = AudioBlock(
                        type="audio",
                        source=Base64Source(
                            type="base64",
                            data=audio_base64,
                            media_type="audio/pcm;rate=24000",
                        ),
                    )
                    return TTSResponse(content=[audio_block])
                else:
                    # No audio data returned
                    # Clear text buffer for this message
                    if msg_id in self._text_buffer:
                        del self._text_buffer[msg_id]

                    audio_block = AudioBlock(
                        type="audio",
                        source=Base64Source(
                            type="base64",
                            data="",
                            media_type="audio/pcm;rate=24000",
                        ),
                    )
                    return TTSResponse(content=[audio_block])
            except Exception:
                import traceback

                traceback.print_exc()
                # Clear text buffer for this message on error
                if msg_id in self._text_buffer:
                    del self._text_buffer[msg_id]

                audio_block = AudioBlock(
                    type="audio",
                    source=Base64Source(
                        type="base64",
                        data="",
                        media_type="audio/pcm;rate=24000",
                    ),
                )
                return TTSResponse(content=[audio_block])
        else:
            # Not the last chunk, return empty AudioBlock
            audio_block = AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data="",
                    media_type="audio/pcm;rate=24000",
                ),
            )
            return TTSResponse(content=[audio_block])

    async def close(self) -> None:
        """Close the Gemini TTS model and clean up resources."""
        if self._client is None:
            return

        self._client = None
        self._text_buffer.clear()
