# -*- coding: utf-8 -*-
"""DashScope SDK TTS model implementation using MultiModalConversation API."""
from typing import (
    Any,
    Literal,
    AsyncGenerator,
    Generator,
    TYPE_CHECKING,
)

from ._tts_base import TTSModelBase
from ._tts_response import TTSResponse
from ..message import Msg, AudioBlock, Base64Source
from ..types import JSONSerializableObject

if TYPE_CHECKING:
    from dashscope.api_entities.dashscope_response import (
        MultiModalConversationResponse,
    )

else:
    MultiModalConversationResponse = (
        "dashscope.api_entities.dashscope_response."
        "MultiModalConversationResponse"
    )


class DashScopeTTSModel(TTSModelBase):
    """DashScope TTS model implementation using MultiModalConversation API.
    For more details, please see the `official document
    <https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2879134>`_.
    """

    supports_streaming_input: bool = False
    """Whether the model supports streaming input."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "qwen3-tts-flash",
        voice: Literal["Cherry", "Serena", "Ethan", "Chelsie"]
        | str = "Cherry",
        language_type: str = "Auto",
        stream: bool = True,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the DashScope SDK TTS model.

        .. note:: More details about the parameters, such as `model_name`,
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
               generation, e.g. `temperature`, `seed`.
        """
        super().__init__(model_name=model_name, stream=stream)

        self.api_key = api_key
        self.voice = voice
        self.language_type = language_type
        self.generate_kwargs = generate_kwargs or {}

    async def synthesize(
        self,
        msg: Msg | None = None,
        **kwargs: Any,
    ) -> TTSResponse | AsyncGenerator[TTSResponse, None]:
        """Call the DashScope TTS API to synthesize speech from text.

        Args:
            msg (`Msg | None`, optional):
                The message to be synthesized.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the TTS API call.

        Returns:
            `TTSResponse | AsyncGenerator[TTSResponse, None]`:
                The TTS response or an async generator yielding TTSResponse
                objects in streaming mode.
        """

        if msg is None:
            return TTSResponse(content=None)

        text = msg.get_text_content()

        import dashscope

        # Call DashScope TTS API with streaming mode
        response = dashscope.MultiModalConversation.call(
            model=self.model_name,
            api_key=self.api_key,
            text=text,
            voice=self.voice,
            language_type=self.language_type,
            stream=True,
            **self.generate_kwargs,
            **kwargs,
        )

        if self.stream:
            return self._parse_into_async_generator(response)

        audio_data = ""
        for chunk in response:
            if chunk.output is not None:
                audio_data += chunk.output.audio.data

        res = TTSResponse(
            content=AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data=audio_data,
                    media_type="audio/pcm;rate=24000",
                ),
            ),
        )
        return res

    @staticmethod
    async def _parse_into_async_generator(
        response: Generator[MultiModalConversationResponse, None, None],
    ) -> AsyncGenerator[TTSResponse, None]:
        """Parse the TTS response into an async generator.

        Args:
            response (`Generator[MultiModalConversationResponse, None, None]`):
                The streaming response from DashScope TTS API.

        Returns:
            `AsyncGenerator[TTSResponse, None]`:
                An async generator yielding TTSResponse objects.
        """
        audio_data = ""
        for chunk in response:
            if chunk.output is not None:
                audio = chunk.output.audio
                if audio and audio.data:
                    audio_data += audio.data
                    yield TTSResponse(
                        content=AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                data=audio_data,
                                media_type="audio/pcm;rate=24000",
                            ),
                        ),
                        is_last=False,
                    )
        yield TTSResponse(
            content=AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data=audio_data,
                    media_type="audio/pcm;rate=24000",
                ),
            ),
            is_last=True,
        )
