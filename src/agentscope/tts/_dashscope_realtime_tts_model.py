# -*- coding: utf-8 -*-
"""DashScope Realtime TTS model implementation."""

import threading
from typing import TYPE_CHECKING, Any, Literal

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

try:
    from dashscope.audio.qwen_tts_realtime import (
        QwenTtsRealtime,
        QwenTtsRealtimeCallback,
    )
except ImportError as exc:
    raise ImportError(
        "dashscope package is required for DashScope TTS. "
        "Please install it with: pip install dashscope",
    ) from exc


class _DashScopeRealtimeTTSCallback(QwenTtsRealtimeCallback):
    """DashScope Realtime TTS callback."""

    def __init__(self) -> None:
        """Initialize the DashScope Realtime TTS callback."""
        super().__init__()
        self.finish_event = threading.Event()
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

            elif event_type == "response.done":
                # Response completed, can be used for metrics
                pass

            elif event_type == "session.finished":
                self.finish_event.set()

        except Exception:
            import traceback

            traceback.print_exc()
            self.finish_event.set()

    async def wait_for_complete(self) -> None:
        """Wait for the TTS synthesis to complete."""
        self.finish_event.wait()

    def get_audio_data(self) -> str:
        """Get the accumulated audio data.

        Returns:
            `str`: The base64-encoded audio data.
        """
        return self._audio_data


class DashScopeRealtimeTTSModel(TTSModelBase):
    """DashScope Qwen Realtime TTS Realtime model implementation.
    For more details, please see the `official document
    <https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2938790>`_.
    """

    # This model supports streaming input (can send text incrementally)
    supports_streaming_input: bool = True

    def __init__(
        self,
        api_key: str,
        model_name: str = "qwen3-tts-flash-realtime",
        voice: Literal["Cherry", "Serena", "Ethan", "Chelsie"]
        | str = "Cherry",
        mode: Literal["server_commit", "commit"] = "server_commit",
        cold_start_length: int = 8,
        client_kwargs: dict = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the DashScope TTS model.

        .. note::
            More details about the parameters, such as `model_name`, `voice`,
            and `mode` can be found in the `official document
            <https://bailian.console.aliyun.com/?tab=doc#/doc/?type=model&url=2938790>`_.

        Args:
            api_key (`str`):
                The DashScope API key.
            model_name (`str`, defaults to "qwen-tts-realtime"):
                The TTS model name. Supported models are
                "qwen3-tts-flash-realtime", "qwen-tts-realtime", etc.
            voice (`Literal["Cherry", "Serena", "Ethan", "Chelsie"] | str`,
             defaults to "Cherry".):
                The voice to use. Supported voices are "Cherry", "Serena",
                "Ethan", "Chelsie", etc.
            mode (`Literal["server_commit", "commit"]`, default to "server
            commit"):
                The TTS mode. Defaults to "server_commit". "server_commit"
                indicates that the server will automatically manage text
                segmentation and determine the optimal timing for synthesis.
            cold_start_length (`int`, defaults to `0`):
                The minimum text length (in characters) before sending TTS
                requests. When set to 0, text will be sent immediately.
                When set to a positive value, text will be buffered until
                the accumulated length reaches this threshold. The buffered
                text will always be sent when `last=True` is called, even
                if the threshold is not reached.
            client_kwargs (`dict`, default `None`):
                The extra keyword arguments to initialize the DashScope
                realtime tts client.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
               The extra keyword arguments used in DashScope realtime tts API
               generation.
        """
        super().__init__(model_name=model_name)
        import dashscope

        # Prefix for each message to track incremental text updates
        # Key is msg.id, value is the accumulated text prefix
        self._prefix: dict[str, str] = {}
        # Track sent text length for each message (for cold start)
        # Key is msg.id, value is the length of text that has been sent to TTS
        self._sent_length: dict[str, int] = {}
        dashscope.api_key = api_key

        # Save callback reference (for DashScope SDK)
        self._dashscope_callback = _DashScopeRealtimeTTSCallback()

        # Store configuration
        self.voice = voice
        self.mode = mode
        self.cold_start_length = cold_start_length

        # Initialize TTS client
        self._tts_client: QwenTtsRealtime | None = None
        self._connected = False
        # Track if any text has been sent to the server
        self._has_text_sent = False

        self.client_kwargs = client_kwargs or {}
        self.generate_kwargs = generate_kwargs or {}

    async def initialize(self) -> None:
        """Initialize the DashScope TTS model and establish connection."""
        # Check if connection is actually alive
        if self._connected and self._tts_client is not None:
            # Verify connection is still alive by checking WebSocket state
            try:
                if hasattr(self._tts_client, "ws") and self._tts_client.ws:
                    # Connection exists and appears to be open
                    return
            except Exception:
                # Connection check failed, need to reconnect
                self._connected = False
                if self._tts_client:
                    try:
                        self._tts_client.close()
                    except Exception:
                        pass
                    self._tts_client = None

        if self._connected:
            return

        if self._tts_client is None:
            self._tts_client = QwenTtsRealtime(
                model=self.model_name,
                callback=self._dashscope_callback,
                **self.client_kwargs,
            )

        self._tts_client.connect()

        # Update session with voice and format settings
        self._tts_client.update_session(
            voice=self.voice,
            mode=self.mode,
            **self.generate_kwargs,
        )

        self._connected = True
        self._has_text_sent = False  # Reset text sent flag when initializing

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
        # Auto-initialize if not connected
        if not self._connected or self._tts_client is None:
            await self.initialize()

        msg_id = msg.id
        # Initialize prefix and sent length for this message if not exists
        if msg_id not in self._prefix:
            self._prefix[msg_id] = ""
            self._sent_length[msg_id] = 0

        # Collect all text blocks and calculate total text
        total_text = ""
        for block in msg.get_content_blocks():
            if block["type"] == "text":
                text = block["text"]
                prefix = self._prefix[msg_id]
                delta = text[len(prefix) :]
                total_text += delta
                self._prefix[msg_id] = text

        # Get current accumulated text length
        current_length = len(self._prefix[msg_id])
        sent_length = self._sent_length[msg_id]
        unsent_length = current_length - sent_length

        # Determine if we should send text based on whether it's the first send
        if sent_length == 0:
            # First send: apply cold_start_length check
            should_send = (
                self.cold_start_length == 0
                or unsent_length >= self.cold_start_length
                or last
            )
        else:
            # Subsequent sends: no cold_start check, only check if there's
            # data to send, or it's the last chunk
            should_send = unsent_length > 0 or last

        if should_send and unsent_length > 0:
            # Send text from sent_length to current_length
            text_to_send = self._prefix[msg_id][sent_length:]
            if text_to_send:
                self._tts_client.append_text(text_to_send)
                self._has_text_sent = True  # Mark that text has been sent
                self._sent_length[msg_id] = current_length

        if last:
            # Ensure all remaining text is sent (in case last=True but
            # threshold not reached)
            # Update current_length in case it changed
            current_length = len(self._prefix[msg_id])
            sent_length = self._sent_length[msg_id]
            if current_length > sent_length:
                remaining_text = self._prefix[msg_id][sent_length:]
                if remaining_text:
                    self._tts_client.append_text(remaining_text)
                    self._has_text_sent = True  # Mark that text has been sent
                    self._sent_length[msg_id] = current_length

            await self._commit()
            self._tts_client.finish()
            await self._dashscope_callback.wait_for_complete()

            # Clean up prefix and sent length for this message
            if msg_id in self._prefix:
                del self._prefix[msg_id]
            if msg_id in self._sent_length:
                del self._sent_length[msg_id]

        return await self.get_tts_response()

    async def get_tts_response(self) -> TTSResponse:
        """Get a TTSResponse from the collected audio data.

        This method collects audio fragments from the audio handler and returns
        a TTSResponse. If no audio data is available, returns a TTSResponse
        with empty audio block.

        Returns:
            `TTSResponse`:
                The TTSResponse containing audio blocks.
        """
        # Get audio data through public method
        audio_base64 = self._dashscope_callback.get_audio_data()

        # Create AudioBlock, even if data is empty
        audio_block = AudioBlock(
            type="audio",
            source=Base64Source(
                type="base64",
                data=audio_base64 or "",
                media_type="audio/pcm;rate=24000",
            ),
        )

        return TTSResponse(content=[audio_block])

    async def _commit(self) -> None:
        """Commit the current text for synthesis."""
        if not self._connected or self._tts_client is None:
            raise RuntimeError(
                "TTS model is not initialized. Call initialize() first.",
            )

        self._tts_client.commit()

    async def close(self) -> None:
        """Close the TTS model and clean up resources."""
        if not self._connected:
            return

        if self._tts_client:
            self._tts_client.close()
            self._tts_client = None

        self._connected = False
        self._has_text_sent = False  # Reset text sent flag
