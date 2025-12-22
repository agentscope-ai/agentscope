# -*- coding: utf-8 -*-
"""Real-time DashScope voice model implementation.

A pure model layer that only handles DashScope API interaction.
Audio playback is handled by the upper-layer VoiceAgent.
"""

import asyncio
import threading
import base64
from typing import Any, Optional, AsyncGenerator, Callable

from agentscope._logging import logger

from ._voice_model_base import VoiceModelBase

try:
    from dashscope.audio.qwen_omni import (
        OmniRealtimeConversation,
        MultiModality,
        AudioFormat,
        OmniRealtimeCallback,
    )

except ImportError:
    OmniRealtimeConversation = None
    MultiModality = None
    AudioFormat = None
    OmniRealtimeCallback = None


class RealtimeDashScopeCallback(OmniRealtimeCallback):
    """DashScope callback that puts responses into async queues.

    Not responsible for audio playback, notifies upper layer through callbacks.
    """

    def __init__(
        self,
        event_loop: Optional[asyncio.AbstractEventLoop] = None,
        on_audio_delta: Optional[Callable[[bytes], None]] = None,
        on_speech_started: Optional[Callable[[], None]] = None,
        on_response_done: Optional[Callable[[], None]] = None,
    ) -> None:
        """Initialize the callback handler.

        Args:
            event_loop (`Optional[asyncio.AbstractEventLoop]`, defaults to
            `None`):
                The event loop to use for async operations.
            on_audio_delta (`Optional[Callable[[bytes], None]]`, defaults
            to `None`):
                Callback when audio delta is received.
            on_speech_started (`Optional[Callable[[], None]]`, defaults to
            `None`):
                Callback when user starts speaking.
            on_response_done (`Optional[Callable[[], None]]`, defaults to
            `None`):
                Callback when response is complete.

        Raises:
            `ImportError`:
                If dashscope is not installed.
        """
        super().__init__()

        self.event_loop = event_loop

        # Callbacks registered by upper layer
        self._on_audio_delta = on_audio_delta
        self._on_speech_started = on_speech_started
        self._on_response_done = on_response_done

        # Response queues
        self.text_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        self.audio_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

        self.complete_event = threading.Event()
        self.conversation: Optional[Any] = None

        self.response_text = ""
        self.response_audio = ""

        self._response_cancelled = False
        self._is_responding = False
        self._user_speaking = False

    @property
    def is_responding(self) -> bool:
        """Check if the model is currently generating a response.

        Returns:
            `bool`:
                True if responding, False otherwise.
        """
        return self._is_responding

    def set_response_cancelled(self, cancelled: bool = True) -> None:
        """Set the response cancelled flag.

        Args:
            cancelled (`bool`, defaults to `True`):
                Whether the response is cancelled.
        """
        self._response_cancelled = cancelled

    def put_to_queue(self, queue: asyncio.Queue[Any], item: Any) -> None:
        """Put item to queue in a thread-safe manner.

        Args:
            queue (`asyncio.Queue[Any]`):
                The queue to put item into.
            item (`Any`):
                The item to put into the queue.
        """
        if self.event_loop and not self.event_loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(
                    queue.put(item),
                    self.event_loop,
                )
            except Exception as e:
                logger.warning("Failed to put to queue: %s", e)

    def _put_to_queue(self, queue: asyncio.Queue[Any], item: Any) -> None:
        """Deprecated: Use put_to_queue instead."""
        self.put_to_queue(queue, item)

    def on_open(self) -> None:
        """Handle connection opened event."""
        logger.info("DashScope connection opened")

    def on_close(self, close_status_code: int, close_msg: str) -> None:
        """Handle connection closed event.

        Args:
            close_status_code (`int`):
                The close status code.
            close_msg (`str`):
                The close message.
        """
        logger.info(
            "DashScope connection closed: code=%d, msg=%s",
            close_status_code,
            close_msg,
        )
        self._put_to_queue(self.text_queue, None)
        self._put_to_queue(self.audio_queue, None)
        self.complete_event.set()

    # pylint: disable=too-many-branches
    def on_event(self, response: dict[str, Any]) -> None:
        """Handle DashScope events.

        Args:
            response (`dict[str, Any]`):
                The event response from DashScope.
        """
        try:
            event_type = response.get("type", "")
            # Debug: log all event types except frequent ones
            if event_type not in [
                "response.audio.delta",
                "response.audio_transcript.delta",
            ]:
                logger.debug("Event: %s", event_type)

            if event_type == "input_audio_buffer.speech_started":
                self._user_speaking = True
                logger.info("Speech started")
                # Notify upper layer: user starts speaking (for interruption)
                if self._is_responding and self._on_speech_started:
                    self._on_speech_started()
                    self._response_cancelled = True

            elif event_type == "input_audio_buffer.speech_stopped":
                self._user_speaking = False
                logger.info("Speech stopped")

            elif (
                event_type
                == "conversation.item.input_audio_transcription.completed"
            ):
                transcript = response.get("transcript", "")
                logger.info("User said: %s", transcript)

            elif event_type == "response.created":
                self._is_responding = True
                self._response_cancelled = False
                self.response_text = ""
                self.response_audio = ""
                logger.info("Response created")

            elif event_type == "response.audio_transcript.delta":
                if self._response_cancelled:
                    return
                text = response.get("delta", "")
                self.response_text += text
                self._put_to_queue(self.text_queue, text)

            elif event_type == "response.audio.delta":
                if self._response_cancelled:
                    return
                audio = response.get("delta", "")
                self.response_audio += audio
                self._put_to_queue(self.audio_queue, audio)
                # Notify upper layer: audio data received (for playback)
                if self._on_audio_delta:
                    try:
                        audio_bytes = base64.b64decode(audio)
                        self._on_audio_delta(audio_bytes)
                    except Exception:
                        pass

            elif event_type == "response.done":
                self._is_responding = False
                logger.info("Response done")
                if not self._response_cancelled:
                    # Notify upper layer: response complete (for waiting
                    # playback)
                    if self._on_response_done:
                        self._on_response_done()
                    self.complete_event.set()
                    self._put_to_queue(self.text_queue, None)
                    self._put_to_queue(self.audio_queue, None)

        except Exception as e:
            logger.error("Callback error: %s", e)

    def reset(self) -> None:
        """Reset the callback state and clear queues."""
        self._response_cancelled = False
        self.response_text = ""
        self.response_audio = ""
        self.complete_event = threading.Event()
        # Clear queues
        for q in [self.text_queue, self.audio_queue]:
            while True:
                try:
                    q.get_nowait()
                except Exception:
                    break


class DashScopeVoiceModel(VoiceModelBase):
    """Real-time DashScope voice model implementation.

    A pure model layer that only handles:
    - Sending text/audio to the model
    - Receiving text/audio responses from the model

    Audio playback is handled by the upper-layer VoiceAgent.
    """

    def __init__(
        self,
        api_key: str,
        model_name: str = "qwen3-omni-flash-realtime",
        voice: str = "Cherry",
        sys_prompt: str = "You are a helpful assistant.",
        enable_turn_detection: bool = True,
    ) -> None:
        """Initialize the DashScope voice model.

        Args:
            api_key (`str`):
                The DashScope API key.
            model_name (`str`, defaults to `"qwen3-omni-flash-realtime"`):
                The name of the DashScope model to use.
             voice (`str`, defaults to `"Cherry"`):
                The voice style to use for audio responses.
            sys_prompt (`str`, defaults to `"You are a helpful assistant."`):
                The system prompt for the model.
            enable_turn_detection (`bool`, defaults to `True`):
                Whether to enable automatic turn detection.

        Raises:
            `ImportError`:
                If dashscope is not installed.
            `ValueError`:
                If API key is not provided.
        """
        import dashscope

        self.model_name = model_name
        self.voice = voice
        self.sys_prompt = sys_prompt
        self.enable_turn_detection = enable_turn_detection

        dashscope.api_key = api_key

        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self.callback: Optional[RealtimeDashScopeCallback] = None
        self.conversation: Optional[Any] = None
        self._initialized = False

        # Callbacks registered by upper layer (used during initialize)
        self._on_audio_delta: Optional[Callable[[bytes], None]] = None
        self._on_speech_started: Optional[Callable[[], None]] = None
        self._on_response_done: Optional[Callable[[], None]] = None

    def set_audio_callbacks(
        self,
        on_audio_delta: Optional[Callable[[bytes], None]] = None,
        on_speech_started: Optional[Callable[[], None]] = None,
        on_response_done: Optional[Callable[[], None]] = None,
    ) -> None:
        """Set audio-related callbacks (call before initialize).

        Args:
            on_audio_delta (`Optional[Callable[[bytes], None]]`, defaults
            to `None`):
                Callback when audio data is received.
            on_speech_started (`Optional[Callable[[], None]]`, defaults to
            `None`):
                Callback when user starts speaking.
            on_response_done (`Optional[Callable[[], None]]`, defaults to
            `None`):
                Callback when response is complete.
        """
        self._on_audio_delta = on_audio_delta
        self._on_speech_started = on_speech_started
        self._on_response_done = on_response_done

    async def initialize(self) -> None:
        """Initialize the model connection and session.

        Sets up the DashScope conversation, establishes WebSocket connection,
        and configures session parameters.
        """
        if self._initialized:
            return

        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._event_loop = asyncio.get_event_loop()

        self.callback = RealtimeDashScopeCallback(
            event_loop=self._event_loop,
            on_audio_delta=self._on_audio_delta,
            on_speech_started=self._on_speech_started,
            on_response_done=self._on_response_done,
        )

        self.conversation = OmniRealtimeConversation(
            model=self.model_name,
            callback=self.callback,
        )
        self.callback.conversation = self.conversation

        self.conversation.connect()

        session_kwargs = {
            "output_modalities": [MultiModality.AUDIO, MultiModality.TEXT],
            "voice": self.voice,
            "input_audio_format": AudioFormat.PCM_16000HZ_MONO_16BIT,
            "output_audio_format": AudioFormat.PCM_24000HZ_MONO_16BIT,
            "enable_input_audio_transcription": True,
            "input_audio_transcription_model": "gummy-realtime-v1",
            "enable_turn_detection": self.enable_turn_detection,
            "instructions": self.sys_prompt,
        }

        self.conversation.update_session(**session_kwargs)
        self._initialized = True
        logger.info("DashScopeVoiceModel initialized")

    def append_audio(self, audio_data: bytes) -> None:
        """Append audio data to the input buffer.

        Args:
            audio_data (`bytes`):
                PCM audio data (16kHz, 16bit, mono).

        Raises:
            `RuntimeError`:
                If not initialized.
        """
        if not self.conversation:
            raise RuntimeError("Not initialized")

        audio_base64 = base64.b64encode(audio_data).decode("ascii")
        # chunk_size must be multiple of 4 (base64 requirement)
        chunk_size = 4096  # Multiple of 4
        for i in range(0, len(audio_base64), chunk_size):
            self.conversation.append_audio(audio_base64[i : i + chunk_size])

    async def send_text(self, text: str) -> None:
        """Send text message to trigger model response.

        Args:
            text (`str`):
                The text content to send.

        Raises:
            `RuntimeError`:
                If not initialized.
        """
        if not self.conversation:
            raise RuntimeError("Not initialized")

        self.callback.reset()
        self.conversation.commit()
        self.conversation.create_response(
            instructions=text,
            output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
        )

    async def iter_text_fragments(self) -> AsyncGenerator[str, None]:
        """Iterate over text fragments from the model response.

        Yields:
            `str`:
                Text fragments as they are received.
        """
        while True:
            frag = await self.callback.text_queue.get()
            if frag is None:
                break
            yield frag

    async def iter_audio_fragments(self) -> AsyncGenerator[bytes, None]:
        """Iterate over audio fragments from the model response.

        Yields:
            `bytes`:
                PCM audio data (24kHz, 16bit, mono).
        """
        accumulated = ""
        while True:
            frag = await self.callback.audio_queue.get()
            if frag is None:
                if accumulated:
                    try:
                        yield base64.b64decode(accumulated)
                    except Exception as e:
                        logger.error("Decode error: %s", e)
                break
            accumulated += frag

    async def cancel_response(self) -> None:
        """Cancel the current response generation."""
        if self.callback:
            self.callback.set_response_cancelled(True)
            self.callback.put_to_queue(self.callback.text_queue, None)
            self.callback.put_to_queue(self.callback.audio_queue, None)

    @property
    def is_connected(self) -> bool:
        """Check if the WebSocket connection is still valid.

        Returns:
            `bool`:
                True if connected, False otherwise.
        """
        if not self.conversation:
            logger.debug("is_connected: no conversation")
            return False
        if not self._initialized:
            logger.debug("is_connected: not initialized")
            return False
        try:
            ws = getattr(self.conversation, "ws", None)
            if ws is None:
                logger.debug("is_connected: ws is None")
                return False
            connected = getattr(ws, "connected", False)
            if not connected:
                logger.debug("is_connected: ws.connected=%s", connected)
            return connected
        except Exception as e:
            logger.debug("is_connected: exception %s", e)
            return False

    async def reconnect(self) -> None:
        """Reconnect to DashScope service.

        Closes existing connection if any, then establishes a new one.
        """
        logger.info("Reconnecting to DashScope...")
        # Close old connection first
        if self.conversation:
            try:
                self.conversation.close()
            except Exception:
                pass
        self.conversation = None
        self._initialized = False
        # Wait a bit before reconnecting
        await asyncio.sleep(0.5)
        await self.initialize()

    async def close(self) -> None:
        """Close the connection and clean up resources."""
        if self.conversation:
            try:
                self.conversation.close()
            except Exception as e:
                logger.error("Close error: %s", e)
        self._initialized = False
        logger.info("DashScopeVoiceModel closed")

    @property
    def is_responding(self) -> bool:
        """Check if the model is currently generating a response.

        Returns:
            `bool`:
                True if responding, False otherwise.
        """
        return self.callback.is_responding if self.callback else False
