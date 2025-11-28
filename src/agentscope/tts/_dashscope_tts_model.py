# -*- coding: utf-8 -*-
"""DashScope SDK TTS model implementation using MultiModalConversation API."""
from typing import Any, Literal

from ._tts_base import TTSModelBase
from ._tts_response import TTSResponse
from ..message import Msg, AudioBlock, Base64Source
from ..types import JSONSerializableObject


class DashScopeTTSModel(TTSModelBase):
    """DashScope TTS model implementation using MultiModalConversation API.
    For more details, please see the `official document
    <https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2879134>`_.
    """

    # This model does not support streaming input (requires complete text)
    supports_streaming_input: bool = False

    def __init__(
        self,
        api_key: str,
        model_name: str = "qwen3-tts-flash",
        voice: Literal["Cherry", "Serena", "Ethan", "Chelsie"]
        | str = "Cherry",
        language_type: str = "Auto",
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the DashScope SDK TTS model.

        .. note::
            More details about the parameters, such as `model_name`,
            `voice`, and language_type can be found in the `official document
            <https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2879134>`_.

        Args:
            api_key (`str`):
                The DashScope API key. Required.
            model_name (`str`, defaults to "qwen3-tts-flash"):
                The TTS model name. Supported models are qwen3-tts-flash,
                qwen-tts, etc.
            voice (`Literal["Cherry", "Serena", "Ethan", "Chelsie"] | str`, \
             defaults to "Cherry"):
                The voice to use. Supported voices are "Cherry", "Serena",
                "Ethan", "Chelsie", etc.
            language_type (`str`, default to "Auto"):
                The language type. Should match the text language for
                correct pronunciation and natural intonation.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
               The extra keyword arguments used in Dashscope TTS API
               generation,
               e.g. `temperature`, `seed`.
        """
        super().__init__(model_name=model_name, stream=True)

        self.api_key = api_key
        self.voice = voice
        self.language_type = language_type
        self._initialized = False
        # Text buffer for each message to accumulate text before synthesis
        # Key is msg.id, value is the accumulated text
        self._text_buffer: dict[str, str] = {}
        self.generate_kwargs = generate_kwargs or {}

    async def initialize(self) -> None:
        """Initialize the DashScope SDK TTS model."""
        if self._initialized:
            return

        self._initialized = True

    # pylint: disable=too-many-branches
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
        if not self._initialized:
            raise RuntimeError(
                "TTS model is not initialized. Call initialize() first.",
            )
        import dashscope

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
                # Call DashScope TTS API with streaming mode
                response = dashscope.MultiModalConversation.call(
                    model=self.model_name,
                    api_key=self.api_key,
                    text=self._text_buffer[msg_id],
                    voice=self.voice,
                    language_type=self.language_type,
                    stream=True,
                    **self.generate_kwargs,
                    **kwargs,
                )

                # Collect all audio chunks from streaming response
                audio_chunks = []
                for chunk in response:
                    if chunk.output is not None:
                        audio = chunk.output.audio
                        if audio and audio.data:
                            audio_chunks.append(audio.data)

                combined_audio = (
                    "".join(audio_chunks) if audio_chunks else None
                )

                if combined_audio:
                    # Clear text buffer for this message
                    del self._text_buffer[msg_id]

                    audio_block = AudioBlock(
                        type="audio",
                        source=Base64Source(
                            type="base64",
                            data=combined_audio,
                            media_type="audio/pcm;rate=24000",
                        ),
                    )
                    return TTSResponse(content=[audio_block])
                else:
                    # Clear text buffer for this message
                    if msg_id in self._text_buffer:
                        del self._text_buffer[msg_id]
                    raise RuntimeError(
                        "DashScope TTS API returned no audio data in "
                        "streaming mode.",
                    )

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
        """Close the DashScope SDK TTS model and clean up resources."""
        if not self._initialized:
            return

        self._initialized = False
        self._text_buffer.clear()
