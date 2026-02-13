# -*- coding: utf-8 -*-
"""DashScope CosyVoice Realtime TTS model implementation."""

from typing import Any, Literal, AsyncGenerator

from ._tts_base import TTSModelBase
from ._tts_response import TTSResponse
from ._utils import _get_cosyvoice_callback_class
from ..message import Msg
from ..types import JSONSerializableObject


class DashScopeCosyVoiceRealtimeTTSModel(TTSModelBase):
    """TTS implementation for DashScope CosyVoice Realtime TTS API,
    which supports streaming input. The supported models include
    "cosyvoice-v3-plus", "cosyvoice-v3-flash", "sambert" etc.

    For more details, please see the `official document
    <https://help.aliyun.com/zh/model-studio/text-to-speech>`_.

    .. note:: The DashScopeCosyVoiceRealtimeTTSModel can only handle one
    streaming input request at a time, and cannot process multiple
    streaming input requests concurrently. For example, it cannot handle
    input sequences like `[msg_1_chunk0, msg_1_chunk1, msg_2_chunk0]`,
    where the prefixes "msg_x" indicate different streaming input requests.
    """

    supports_streaming_input: bool = True
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
        stream: bool = True,
        cold_start_length: int | None = None,
        cold_start_words: int | None = None,
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
        max_retries: int = 3,
        retry_delay: float = 5.0,
    ) -> None:
        """Initialize the DashScope CosyVoice Realtime TTS model by
        specifying the model, voice, and other parameters.

        .. note:: More details about the parameters, such as `model_name`,
        `voice`, and `mode` can be found in the `official document
        <https://help.aliyun.com/zh/model-studio/cosyvoice-voice-list>`_.

        .. note:: You can use `cold_start_length` and `cold_start_words`
        simultaneously to set both character and word thresholds for the first
        TTS request. For Chinese text, word segmentation (based on spaces) may
        not be effective.

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
            stream (`bool`, defaults to `True`):
                Whether to use streaming synthesis.
            cold_start_length (`int | None`, optional):
                The minimum length send threshold for the first TTS request,
                ensuring there is no pause in the synthesized speech for too
                short input text. The length is measured in number of
                characters.
            cold_start_words (`int | None`, optional):
                The minimum words send threshold for the first TTS request,
                ensuring there is no pause in the synthesized speech for too
                short input text. The words are identified by spaces in the
                text.
            client_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments to initialize the DashScope
                CosyVoice Realtime tts client.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
               The extra keyword arguments used in DashScope CosyVoice
               Realtime tts API generation.
            max_retries (`int`, defaults to 3):
                The maximum number of retry attempts when TTS synthesis fails.
            retry_delay (`float`, defaults to 5.0):
                The delay in seconds before retrying. Uses exponential backoff.
        """
        super().__init__(model_name=model_name, stream=stream)

        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        dashscope.api_key = api_key

        # Store configuration
        self.voice = voice
        self.cold_start_length = cold_start_length
        self.cold_start_words = cold_start_words
        self.client_kwargs = client_kwargs or {}
        self.generate_kwargs = generate_kwargs or {}
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        # Initialize TTS client
        # Save callback reference (for DashScope SDK)
        self._dashscope_callback = _get_cosyvoice_callback_class()()

        # The variables for tracking streaming input messages
        # If we have sent text for the current message
        self._first_send: bool = True
        # The current message ID being processed
        self._current_msg_id: str | None = None
        # The current prefix text already sent
        self._current_prefix: str = ""
        self._synthesizer: SpeechSynthesizer | None = None

    async def connect(self) -> None:
        """Connect to the TTS model and initialize resources."""
        from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat

        self._synthesizer = SpeechSynthesizer(
            model=self.model_name,
            voice=self.voice,
            format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            callback=self._dashscope_callback,
            **self.client_kwargs,
            **self.generate_kwargs,
        )

    async def close(self) -> None:
        """Close the TTS model and release resources."""
        self._synthesizer.close()

    async def push(
        self,
        msg: Msg,
        **kwargs: Any,
    ) -> TTSResponse:
        """Append text to be synthesized and return the received TTS response.
        Note this method is non-blocking, and maybe return an empty response
        if no audio is received yet.

        To receive all the synthesized speech, call the `synthesize` method
        after pushing all the text chunks.

        Args:
            msg (`Msg`):
                The message to be synthesized. The `msg.id` identifies the
                streaming input request.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the TTS API call.

        Returns:
            `TTSResponse`:
                The TTSResponse containing audio blocks.
        """

        if self._current_msg_id is not None and self._current_msg_id != msg.id:
            raise RuntimeError(
                "DashScopeCosyVoiceRealtimeTTSModel can only handle one "
                "streaming input request at a time. Please ensure that all "
                "chunks belong to the same message ID.",
            )

        # Record current message ID
        self._current_msg_id = msg.id

        text = msg.get_text_content()

        # Determine if we should send text based on cold start settings only
        # for the first input chunk and not the last chunk
        if text:
            if self._first_send:
                # If we have cold start settings
                if self.cold_start_length:
                    if len(text) < self.cold_start_length:
                        delta_to_send = ""
                    else:
                        delta_to_send = text
                else:
                    delta_to_send = text

                if delta_to_send and self.cold_start_words:
                    if len(delta_to_send.split()) < self.cold_start_words:
                        delta_to_send = ""
            else:
                # Remove the already sent prefix if not the first send
                delta_to_send = text.removeprefix(self._current_prefix)

            if delta_to_send:
                self._synthesizer.streaming_call(delta_to_send)

                # Record sent prefix
                self._current_prefix += delta_to_send
                self._first_send = False

            # Wait for the audio data to be available
            res = await self._dashscope_callback.get_audio_data(block=False)

            return res

        # Return empty response if no text to send
        return TTSResponse(content=None)

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
        if (
            self._current_msg_id is not None
            and msg
            and self._current_msg_id != msg.id
        ):
            raise RuntimeError(
                "DashScopeCosyVoiceRealtimeTTSModel can only handle one "
                "streaming input request at a time. Please ensure that all "
                "chunks belong to the same message ID.",
            )

        if msg is None:
            delta_to_send = ""

        else:
            # Record current message ID
            self._current_msg_id = msg.id
            delta_to_send = (msg.get_text_content() or "").removeprefix(
                self._current_prefix,
            )

        # Determine if we should send text based on cold start settings only
        # for the first input chunk and not the last chunk
        if delta_to_send:
            self._synthesizer.streaming_call(delta_to_send)

            # To keep correct prefix tracking
            self._current_prefix += delta_to_send
            self._first_send = False

        # We need to block until synthesis is complete to get all audio
        self._synthesizer.streaming_complete()

        if self.stream:
            # Return an async generator for audio chunks
            res = self._dashscope_callback.get_audio_chunk()

        else:
            # Block and wait for all audio data to be available
            res = await self._dashscope_callback.get_audio_data(block=True)

        # Update state for next message
        self._current_msg_id = None
        self._first_send = True
        self._current_prefix = ""

        return res
