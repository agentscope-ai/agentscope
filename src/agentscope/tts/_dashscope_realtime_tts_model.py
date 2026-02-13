# -*- coding: utf-8 -*-
"""DashScope Realtime TTS model implementation."""

import threading
from typing import Any, Literal, TYPE_CHECKING, AsyncGenerator

from ._tts_base import TTSModelBase
from ._tts_response import TTSResponse
from ..message import Msg, AudioBlock, Base64Source
from ..types import JSONSerializableObject

if TYPE_CHECKING:
    from dashscope.audio.qwen_tts_realtime import (
        QwenTtsRealtime,
        QwenTtsRealtimeCallback,
    )
else:
    QwenTtsRealtime = "dashscope.audio.qwen_tts_realtime.QwenTtsRealtime"
    QwenTtsRealtimeCallback = (
        "dashscope.audio.qwen_tts_realtime.QwenTtsRealtimeCallback"
    )


def _get_qwen_tts_realtime_callback_class() -> type["QwenTtsRealtimeCallback"]:
    from dashscope.audio.qwen_tts_realtime import QwenTtsRealtimeCallback

    class _DashScopeRealtimeTTSCallback(QwenTtsRealtimeCallback):
        """DashScope Realtime TTS callback."""

        def __init__(self) -> None:
            """Initialize the DashScope Realtime TTS callback."""
            super().__init__()

            # The event that will be set when a new audio chunk is received
            self.chunk_event = threading.Event()
            # The event that will be set when the TTS synthesis is finished
            self.finish_event = threading.Event()
            # Cache the audio data
            self._audio_data: str = ""

        def on_event(self, response: dict[str, Any]) -> None:
            """Called when a TTS event is received (DashScope SDK callback).

            Args:
                response (`dict[str, Any]`):
                    The event response dictionary.
            """
            try:
                event_type = response.get("type")

                if event_type == "session.created":
                    self._audio_data = ""
                    self.finish_event.clear()

                elif event_type == "response.audio.delta":
                    audio_data = response.get("delta")
                    if audio_data:
                        # Process audio data in thread callback
                        if isinstance(audio_data, bytes):
                            import base64

                            audio_data = base64.b64encode(audio_data).decode()

                        # Accumulate audio data
                        self._audio_data += audio_data

                        # Signal that a new audio chunk is available
                        if not self.chunk_event.is_set():
                            self.chunk_event.set()

                elif event_type == "response.done":
                    # Response completed, can be used for metrics
                    pass

                elif event_type == "session.finished":
                    self.chunk_event.set()
                    self.finish_event.set()

            except Exception:
                import traceback

                traceback.print_exc()
                self.finish_event.set()

        async def get_audio_data(self, block: bool) -> TTSResponse:
            """Get the current accumulated audio data as base64 string so far.

            Returns:
                `str`:
                    The base64-encoded audio data.
            """
            # Block until synthesis is finished
            if block:
                self.finish_event.wait()

            # Return the accumulated audio data
            if self._audio_data:
                return TTSResponse(
                    content=AudioBlock(
                        type="audio",
                        source=Base64Source(
                            type="base64",
                            data=self._audio_data,
                            media_type="audio/pcm;rate=24000",
                        ),
                    ),
                )

            # Reset for next tts request
            await self._reset()

            # Return empty response if no audio data
            return TTSResponse(content=None)

        async def get_audio_chunk(self) -> AsyncGenerator[TTSResponse, None]:
            """Get the audio data chunk as an async generator of `TTSResponse`
            objects.

            Returns:
                `AsyncGenerator[TTSResponse, None]`:
                    The async generator yielding TTSResponse with audio chunks.
            """
            while True:
                if self.finish_event.is_set():
                    yield TTSResponse(
                        content=AudioBlock(
                            type="audio",
                            source=Base64Source(
                                type="base64",
                                data=self._audio_data,
                                media_type="audio/pcm;rate=24000",
                            ),
                        ),
                        is_last=True,
                    )

                    # Reset for next tts request
                    await self._reset()

                    break

                if self.chunk_event.is_set():
                    # Clear the event for next chunk
                    self.chunk_event.clear()
                else:
                    # Wait for the next chunk
                    self.chunk_event.wait()

                yield TTSResponse(
                    content=AudioBlock(
                        type="audio",
                        source=Base64Source(
                            type="base64",
                            data=self._audio_data,
                            media_type="audio/pcm;rate=24000",
                        ),
                    ),
                    is_last=False,
                )

        async def _reset(self) -> None:
            """Reset the callback state for a new TTS request."""
            self.finish_event.clear()
            self.chunk_event.clear()
            self._audio_data = ""

    return _DashScopeRealtimeTTSCallback


class DashScopeRealtimeTTSModel(TTSModelBase):
    """TTS implementation for DashScope Qwen Realtime TTS API, which supports
    streaming input. The supported models include "qwen-3-tts-flash-realtime",
    "qwen-tts-realtime", etc.

    For more details, please see the `official document
    <https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2938790>`_.

    .. note:: The DashScopeRealtimeTTSModel can only handle one streaming
    input request at a time, and cannot process multiple streaming input
    requests concurrently. For example, it cannot handle input sequences like
    `[msg_1_chunk0, msg_1_chunk1, msg_2_chunk0]`, where the prefixes "msg_x"
    indicate different streaming input requests.
    """

    supports_streaming_input: bool = True
    """Whether the model supports streaming input."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "qwen3-tts-flash-realtime",
        voice: Literal["Cherry", "Nofish", "Ethan", "Jennifer"]
        | str = "Cherry",
        stream: bool = True,
        cold_start_length: int | None = None,
        cold_start_words: int | None = None,
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the DashScope TTS model by specifying the model, voice,
        and other parameters.

        .. note:: More details about the parameters, such as `model_name`,
        `voice`, and `mode` can be found in the `official document
        <https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2938790>`_.

        .. note:: You can use `cold_start_length` and `cold_start_words`
        simultaneously to set both character and word thresholds for the first
        TTS request. For Chinese text, word segmentation (based on spaces) may
        not be effective.

        Args:
            api_key (`str`):
                The DashScope API key.
            model_name (`str`, defaults to "qwen-tts-realtime"):
                The TTS model name, e.g. "qwen3-tts-flash-realtime",
                "qwen-tts-realtime", etc.
            voice (`Literal["Cherry", "Serena", "Ethan", "Chelsie"] | str`, \
             defaults to "Cherry".):
                The voice to use for synthesis. Refer to `official document
                <https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2938790>`_
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
                realtime tts client.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
               The extra keyword arguments used in DashScope realtime tts API
               generation.
        """
        super().__init__(model_name=model_name, stream=stream)

        import dashscope
        from dashscope.audio.qwen_tts_realtime import QwenTtsRealtime

        dashscope.api_key = api_key

        # Store configuration
        self.voice = voice
        self.mode = "server_commit"
        self.cold_start_length = cold_start_length
        self.cold_start_words = cold_start_words
        self.client_kwargs = client_kwargs or {}
        self.generate_kwargs = generate_kwargs or {}

        # Initialize TTS client
        # Save callback reference (for DashScope SDK)
        self._dashscope_callback = _get_qwen_tts_realtime_callback_class()()
        self._tts_client: QwenTtsRealtime = QwenTtsRealtime(
            model=self.model_name,
            callback=self._dashscope_callback,
            **self.client_kwargs,
        )

        self._connected = False

        # The variables for tracking streaming input messages
        # If we have sent text for the current message
        self._first_send: bool = True
        # The current message ID being processed
        self._current_msg_id: str | None = None
        # The current prefix text already sent
        self._current_prefix: str = ""

    async def connect(self) -> None:
        """Initialize the DashScope TTS model and establish connection."""
        if self._connected:
            return

        self._tts_client.connect()

        # Update session with voice and format settings
        self._tts_client.update_session(
            voice=self.voice,
            mode=self.mode,
            **self.generate_kwargs,
        )

        self._connected = True

    async def close(self) -> None:
        """Close the TTS model and clean up resources."""
        if not self._connected:
            return

        self._connected = False

        self._tts_client.finish()
        self._tts_client.close()

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
        if not self._connected:
            raise RuntimeError(
                "TTS model is not connected. Call `connect()` first.",
            )

        if self._current_msg_id is not None and self._current_msg_id != msg.id:
            raise RuntimeError(
                "DashScopeRealtimeTTSModel can only handle one streaming "
                "input request at a time. Please ensure that all chunks "
                "belong to the same message ID.",
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
                self._tts_client.append_text(delta_to_send)

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
        if not self._connected:
            raise RuntimeError(
                "TTS model is not connected. Call `connect()` first.",
            )

        if self._current_msg_id is not None and self._current_msg_id != msg.id:
            raise RuntimeError(
                "DashScopeRealtimeTTSModel can only handle one streaming "
                "input request at a time. Please ensure that all chunks "
                "belong to the same message ID.",
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
            self._tts_client.append_text(delta_to_send)

            # To keep correct prefix tracking
            self._current_prefix += delta_to_send
            self._first_send = False

        # We need to block until synthesis is complete to get all audio
        self._tts_client.commit()
        self._tts_client.finish()

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
