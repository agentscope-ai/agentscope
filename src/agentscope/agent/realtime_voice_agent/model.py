# -*- coding: utf-8 -*-
"""Base class for WebSocket-based real-time voice models with callback pattern.

This module provides a callback-based model architecture where:
- Model receives API events and converts them to ModelEvents
- Agent registers a callback to receive ModelEvents
- Agent converts ModelEvents to AgentEvents for dispatch
"""

import json
import asyncio
import base64
from abc import ABC, abstractmethod
from typing import Any, Callable

import websockets
from websockets.asyncio.client import ClientConnection

from ..._logging import logger

from .events import (
    ModelEvent,
    ModelEventType,
    ModelError,
    ModelWebSocketConnect,
    ModelWebSocketDisconnect,
)


def resample_audio(
    audio_data: bytes,
    from_rate: int,
    to_rate: int,
) -> bytes:
    """Resample audio data from one sample rate to another.

    Uses linear interpolation for resampling. Suitable for real-time
    applications where quality trade-offs are acceptable.

    Args:
        audio_data (`bytes`):
            The PCM audio bytes (16-bit signed, mono).
        from_rate (`int`):
            The source sample rate in Hz.
        to_rate (`int`):
            The target sample rate in Hz.

    Returns:
        `bytes`:
            The resampled PCM audio bytes.

    Example:
        .. code-block:: python

            # Resample 44.1kHz audio to 16kHz
            resampled = resample_audio(audio_data, 44100, 16000)
    """
    import numpy as np

    if from_rate == to_rate:
        return audio_data

    # Convert bytes to numpy array (16-bit signed)
    audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)

    # Calculate the number of samples in the resampled audio
    num_samples = int(len(audio_array) * to_rate / from_rate)

    # Resample using linear interpolation
    indices = np.linspace(0, len(audio_array) - 1, num_samples)
    resampled = np.interp(indices, np.arange(len(audio_array)), audio_array)

    # Convert back to 16-bit signed integer
    return resampled.astype(np.int16).tobytes()


class RealtimeVoiceModelBase(ABC):
    """Base class for WebSocket voice models with callback pattern.

    This class provides:
    - WebSocket connection management
    - Event callback mechanism for ModelEvents
    - State tracking (responding, cancelled)

    The model converts API-specific events to unified ModelEvents and
    invokes the registered callback for each event.

    Usage:
        .. code-block:: python

            model = DashScopeRealtimeModel(api_key="xxx", model_name="xxx")

            def my_callback(event: ModelEvent):
                print(f"Received: {event.type}")
                # Convert to AgentEvent and dispatch...

            model.agent_callback = my_callback
            await model.start()
    """

    def __init__(
        self,
        api_key: str,
        model_name: str,
        voice: str = "Cherry",
        instructions: str = "You are a helpful assistant.",
    ) -> None:
        """Initialize the voice model.

        Args:
            api_key (`str`):
                The API key for authentication.
            model_name (`str`):
                The model name to use.
            voice (`str`, optional):
                The voice style for audio output. Defaults to "Cherry".
            instructions (`str`, optional):
                The system instructions for the model. Defaults to
                "You are a helpful assistant.".
        """
        # Configuration
        self.api_key = api_key
        self.model_name = model_name
        self.voice = voice
        self.instructions = instructions

        # WebSocket connection
        self._websocket: ClientConnection | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._initialized = False

        # Callback for ModelEvents (set by Agent)
        self.agent_callback: Callable[[ModelEvent], None] | None = None

        # State
        self.is_responding = False
        self.response_cancelled = False

        # Event loop reference
        self._event_loop: asyncio.AbstractEventLoop | None = None

    # =========================================================================
    # Abstract Methods - Subclasses Must Implement
    # =========================================================================

    @abstractmethod
    def _get_websocket_url(self) -> str:
        """Get WebSocket endpoint URL.

        Returns:
            `str`:
                The WebSocket endpoint URL.
        """

    @abstractmethod
    def _get_headers(self) -> dict[str, str]:
        """Get authentication headers.

        Returns:
            `dict[str, str]`:
                The authentication headers.
        """

    @abstractmethod
    def _build_session_config(self, **kwargs: Any) -> str:
        """Build session configuration message as JSON string.

        Args:
            **kwargs:
                Additional session configuration parameters.

        Returns:
            `str`:
                The session configuration message as JSON string.
        """

    @abstractmethod
    def _format_audio_message(self, audio_b64: str) -> str:
        """Format audio data for sending as JSON string.

        Args:
            audio_b64 (`str`):
                The base64 encoded audio data.

        Returns:
            `str`:
                The formatted audio message as JSON string.
        """

    @abstractmethod
    def _parse_server_message(self, message: str) -> ModelEvent:
        """Parse server message to ModelEvent.

        Args:
            message (`str`):
                The server message to parse.

        Returns:
            `ModelEvent`:
                The parsed ModelEvent.
        """

    @abstractmethod
    def _format_cancel_message(self) -> str | None:
        """Format cancel response message.

        Returns:
            `str | None`:
                The cancel message, or None if not supported.
        """

    @abstractmethod
    def _format_tool_result_message(
        self,
        tool_id: str,
        tool_name: str,
        result: str,
    ) -> str:
        """Format tool result message.

        Args:
            tool_id (`str`):
                The tool call ID.
            tool_name (`str`):
                The tool name.
            result (`str`):
                The tool execution result.

        Returns:
            `str`:
                The formatted tool result message as JSON string.
        """

    @abstractmethod
    def _format_image_message(
        self,
        image_b64: str,
        mime_type: str = "image/jpeg",
    ) -> str | None:
        """Format image data for sending as JSON string.

        Args:
            image_b64 (`str`):
                The base64 encoded image data (without data URI prefix).
            mime_type (`str`):
                The MIME type of the image. Defaults to "image/jpeg".
                Supported: "image/jpeg", "image/png", "image/webp".

        Returns:
            `str | None`:
                The formatted image message, or None if not supported.
        """

    @abstractmethod
    def _format_text_message(self, text: str) -> str | None:
        """Format text input for sending as JSON string.

        Args:
            text (`str`):
                The text message to send.

        Returns:
            `str | None`:
                The formatted text message, or None if not supported.
        """

    @abstractmethod
    def _format_session_update_message(
        self,
        config: dict[str, Any],
    ) -> str | None:
        """Format session update message as JSON string.

        This is called when the client sends `client.session.update` to
        dynamically update session configuration (voice, instructions, etc.).

        Args:
            config (`dict[str, Any]`):
                The session configuration to update. May include:
                - voice: Voice style for audio output
                - instructions: System instructions
                - input_audio_format: Audio format configuration
                - turn_detection: VAD configuration
                - tools: List of tool JSON schemas (for frontend-executed
                tools)

        Returns:
            `str | None`:
                The formatted session update message, or None if not supported.
        """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Get the provider name (e.g., 'dashscope', 'gemini', 'openai').

        Returns:
            `str`:
                The provider name.
        """

    @property
    def supports_image(self) -> bool:
        """Check if the model supports image input.

        Returns:
            `bool`:
                True if the model supports image input, False otherwise.
        """
        return False  # Override in subclass if supported

    # =========================================================================
    # Connection Management
    # =========================================================================

    async def start(self, **kwargs: Any) -> None:
        """Start the model connection.

        This connects to the WebSocket, starts the receive loop,
        and sends the session configuration.

        Args:
            **kwargs:
                Additional session configuration parameters.
        """
        if self._initialized:
            return

        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._event_loop = asyncio.get_event_loop()

        # Connect to WebSocket
        url = self._get_websocket_url()
        headers = self._get_headers()

        logger.info("Connecting to %s WebSocket...", self.provider_name)

        self._websocket = await websockets.connect(
            url,
            additional_headers=headers,
        )

        logger.info("WebSocket connection opened")

        # Notify connection established
        self._emit_event(ModelWebSocketConnect())

        # Start receive loop
        self._receive_task = asyncio.create_task(self._receive_loop())

        # Send session configuration
        config_msg = self._build_session_config(**kwargs)
        await self._websocket.send(config_msg)

        self._initialized = True
        logger.info("%s model started", self.provider_name)

    async def _receive_loop(self) -> None:
        """Background task to receive and process WebSocket messages.

        This method runs in a loop until the WebSocket connection is closed
        or an error occurs.
        """
        if not self._websocket:
            return

        disconnected = False

        try:
            async for message in self._websocket:
                if isinstance(message, bytes):
                    message = message.decode("utf-8")

                # Parse to ModelEvent
                event = self._parse_server_message(message)

                # Handle internal state (async, may cancel response)
                await self._handle_internal_state(event)

                # Emit to callback
                self._emit_event(event)

        except websockets.exceptions.ConnectionClosed as e:
            logger.info("WebSocket connection closed: %s", e)
            disconnected = True
        except Exception as e:
            logger.error("Error in receive loop: %s", e)
            self._emit_event(
                ModelError(
                    error_type="receive_error",
                    code="RECEIVE_ERROR",
                    message=str(e),
                ),
            )
            disconnected = True
        finally:
            # Only emit disconnect once
            if disconnected or not self._initialized:
                self._emit_event(ModelWebSocketDisconnect())

    async def _handle_internal_state(self, event: ModelEvent) -> None:
        """Handle internal state changes based on event.

        Args:
            event (`ModelEvent`):
                The event to handle.
        """
        event_type = event.type

        if event_type == ModelEventType.RESPONSE_CREATED:
            self.is_responding = True
            self.response_cancelled = False

        elif event_type == ModelEventType.RESPONSE_DONE:
            self.is_responding = False

        elif event_type == ModelEventType.INPUT_STARTED:
            # Speech started (VAD) - cancel current response
            if self.is_responding:
                logger.info("Speech started, cancelling current response")
                await self.cancel_response()

    def _emit_event(self, event: ModelEvent) -> None:
        """Emit a ModelEvent to the registered callback.

        Args:
            event (`ModelEvent`):
                The event to emit.

        .. note::
            If the response has been cancelled, response-related events
            (audio delta, text delta, etc.) will be silently dropped
            to prevent stale data from reaching the frontend.
        """
        # Filter out response events after cancellation
        event_type = event.type

        # RESPONSE_CREATED marks a new response - reset cancellation flag
        if event_type == ModelEventType.RESPONSE_CREATED:
            self.response_cancelled = False

        # Drop content events if response was cancelled
        if self.response_cancelled:
            # Only drop content events (audio, transcript)
            # Allow RESPONSE_CREATED and RESPONSE_DONE
            if event_type in (
                ModelEventType.RESPONSE_AUDIO_DELTA,
                ModelEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA,
            ):
                logger.debug(
                    "Dropping %s event after response cancelled",
                    event_type,
                )
                return

        callback = self.agent_callback
        if callback is not None:
            try:
                callback(event)
            except Exception as e:
                logger.error("Error in agent callback: %s", e, exc_info=True)

    # =========================================================================
    # Audio Operations
    # =========================================================================

    def send_audio(
        self,
        audio_data: bytes,
        sample_rate: int | None = None,
    ) -> None:
        """Send audio data to the model.

        This is a non-blocking call that sends audio to the model.

        Args:
            audio_data (`bytes`):
                The PCM audio bytes.
            sample_rate (`int`, optional):
                The sample rate for resampling if needed. Defaults to None.
        """
        if not self._websocket:
            raise RuntimeError("Model not started")

        # Preprocess audio (subclass can override for resampling)
        audio_data = self._preprocess_audio(audio_data, sample_rate)

        # Encode and format
        audio_b64 = base64.b64encode(audio_data).decode("ascii")
        wire_msg = self._format_audio_message(audio_b64)

        # Send asynchronously
        if self._event_loop and not self._event_loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._websocket.send(wire_msg),
                self._event_loop,
            )

    def _preprocess_audio(
        self,
        audio_data: bytes,
        sample_rate: int | None,  # pylint: disable=unused-argument
    ) -> bytes:
        """Hook for subclasses to preprocess audio (e.g., resample).

        Args:
            audio_data (`bytes`):
                The raw audio data.
            sample_rate (`int`, optional):
                The sample rate of the audio.

        Returns:
            `bytes`:
                The preprocessed audio data.
        """
        return audio_data

    # =========================================================================
    # Image Operations
    # =========================================================================

    def send_image(
        self,
        image_data: bytes,
        mime_type: str = "image/jpeg",
    ) -> None:
        """Send image data to the model.

        This is a non-blocking call that sends image to the model.

        Args:
            image_data (`bytes`):
                The image bytes.
            mime_type (`str`):
                The MIME type of the image. Defaults to "image/jpeg".
                Supported: "image/jpeg", "image/png", "image/webp".

        .. note::
            - Recommended format: JPEG. Resolution: 480P or 720P, max 1080P.
            - Single image should not exceed 500KB.
            - Recommended frequency: 1 image per second.
            - Must send audio data before sending images.
        """
        if not self._websocket:
            raise RuntimeError("Model not started")

        if not self.supports_image:
            logger.warning(
                "%s model does not support image input",
                self.provider_name,
            )
            return

        # Encode and format
        image_b64 = base64.b64encode(image_data).decode("ascii")
        wire_msg = self._format_image_message(image_b64, mime_type)

        if wire_msg is None:
            logger.warning("Image message format not implemented")
            return

        # Send asynchronously
        if self._event_loop and not self._event_loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._websocket.send(wire_msg),
                self._event_loop,
            )

    # =========================================================================
    # Text Operations
    # =========================================================================

    def send_text(self, text: str) -> None:
        """Send text input to the model.

        This is a non-blocking call that sends text to the model.
        The model will respond with audio output.

        Args:
            text (`str`):
                The text message to send.
        """
        if not self._websocket:
            raise RuntimeError("Model not started")

        wire_msg = self._format_text_message(text)

        if wire_msg is None:
            logger.warning(
                "%s model does not support text input",
                self.provider_name,
            )
            return

        # Send asynchronously
        if self._event_loop and not self._event_loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._websocket.send(wire_msg),
                self._event_loop,
            )

    # =========================================================================
    # Session Update
    # =========================================================================

    async def update_session(self, config: dict[str, Any]) -> None:
        """Update session configuration dynamically.

        This is called when the client sends `client.session.update` to
        modify the session configuration at runtime.

        Args:
            config (`dict[str, Any]`):
                The session configuration to update. May include:
                - voice: Voice style for audio output
                - instructions: System instructions
                - input_audio_format: Audio format configuration
                - turn_detection: VAD configuration
                - tools: List of tool JSON schemas

        Example:
            .. code-block:: python

                await model.update_session({
                    "voice": "Cherry",
                    "instructions": "You are a helpful assistant.",
                    "turn_detection": {"type": "server_vad"},
                })
        """
        if not self._websocket:
            raise RuntimeError("Model not started")

        wire_msg = self._format_session_update_message(config)

        if wire_msg is None:
            logger.warning(
                "%s model does not support session update",
                self.provider_name,
            )
            return

        await self._websocket.send(wire_msg)
        logger.info("Session update sent: %s", list(config.keys()))

    # =========================================================================
    # Response Control
    # =========================================================================

    async def create_response(self) -> None:
        """Trigger model to generate a response (for non-VAD mode).

        This is a no-op by default. Subclasses should override this method
        to implement response triggering.
        """
        # Default: no-op, override in subclasses

    async def cancel_response(self) -> None:
        """Cancel the current response.

        Sets the response_cancelled flag and sends a cancel message
        to the model if supported.
        """
        self.response_cancelled = True
        self.is_responding = False

        cancel_msg = self._format_cancel_message()
        if cancel_msg and self._websocket:
            await self._websocket.send(cancel_msg)

    async def send_tool_result(
        self,
        tool_id: str,
        tool_name: str,
        result: str | dict | list,
    ) -> None:
        """Send tool execution result back to the model.

        Args:
            tool_id (`str`):
                The tool call ID.
            tool_name (`str`):
                The tool name.
            result (`str | dict | list`):
                The tool execution result.
        """
        if not self._websocket:
            raise RuntimeError("Model not started")

        # Convert result to string
        if isinstance(result, (dict, list)):
            result_str = json.dumps(result)
        else:
            result_str = str(result)

        wire_msg = self._format_tool_result_message(
            tool_id,
            tool_name,
            result_str,
        )
        await self._websocket.send(wire_msg)
        logger.info("Tool result sent: %s", tool_name)

    # =========================================================================
    # Connection Properties
    # =========================================================================

    @property
    def is_connected(self) -> bool:
        """Check if connected.

        Returns:
            `bool`:
                True if connected, False otherwise.
        """
        return (
            self._websocket is not None
            and self._websocket.state.name == "OPEN"
            and self._initialized
        )

    async def close(self) -> None:
        """Close the model connection.

        This method cancels the receive task and closes the WebSocket
        connection.
        """
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._websocket:
            try:
                await self._websocket.close()
            except Exception as e:
                logger.error("Close error: %s", e)

        self._initialized = False
        logger.info("%s model closed", self.provider_name)

    def reset(self) -> None:
        """Reset state for new response.

        Clears the is_responding and response_cancelled flags.
        """
        self.is_responding = False
        self.response_cancelled = False
