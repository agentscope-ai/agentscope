# -*- coding: utf-8 -*-
"""Base class for WebSocket-based real-time voice models with callback pattern.

This module provides a callback-based model architecture where:
- Model receives API events and converts them to ModelEvents
- Agent registers a callback to receive ModelEvents
- Agent converts ModelEvents to AgentEvents for dispatch
"""

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

# Type alias for event callback
ModelEventCallback = Callable[[ModelEvent], None]


class RealtimeVoiceModelBase(ABC):
    """Base class for WebSocket voice models with callback pattern.

    This class provides:
    - WebSocket connection management
    - Event callback mechanism for ModelEvents
    - State tracking (responding, cancelled)

    The model converts API-specific events to unified ModelEvents and
    invokes the registered callback for each event.

    Usage:
        ```python
        model = DashScopeRealtimeModel(api_key="xxx", model_name="xxx")

        def my_callback(event: ModelEvent):
            print(f"Received: {event.type}")
            # Convert to AgentEvent and dispatch...

        model.agent_callback = my_callback
        await model.start()
        ```
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
            api_key: API key for authentication.
            model_name: Model name to use.
            voice: Voice style for audio output.
            instructions: System instructions for the model.
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
        self.agent_callback: ModelEventCallback | None = None

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
        """Get WebSocket endpoint URL."""

    @abstractmethod
    def _get_headers(self) -> dict[str, str]:
        """Get authentication headers."""

    @abstractmethod
    def _build_session_config(self, **kwargs: Any) -> str:
        """Build session configuration message as JSON string."""

    @abstractmethod
    def _format_audio_message(self, audio_b64: str) -> str:
        """Format audio data for sending as JSON string."""

    @abstractmethod
    def _parse_server_message(self, message: str) -> ModelEvent:
        """Parse server message to ModelEvent."""

    @abstractmethod
    def _format_cancel_message(self) -> str | None:
        """Format cancel response message."""

    @abstractmethod
    def _format_tool_result_message(
        self,
        tool_id: str,
        tool_name: str,
        result: str,
    ) -> str:
        """Format tool result message."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Get the provider name (e.g., 'dashscope', 'gemini', 'openai')."""

    # =========================================================================
    # Connection Management
    # =========================================================================

    async def start(self, **kwargs: Any) -> None:
        """Start the model connection.

        This connects to the WebSocket, starts the receive loop,
        and sends the session configuration.

        Args:
            **kwargs: Additional session configuration parameters.
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
        """Background task to receive and process WebSocket messages."""
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
        """Handle internal state changes based on event."""
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
        """Emit a ModelEvent to the registered callback."""
        callback = self.agent_callback
        if callback is not None:
            try:
                callback(event)
            except Exception as e:
                logger.error("Error in agent callback: %s", e)

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
            audio_data: PCM audio bytes.
            sample_rate: Sample rate (for resampling if needed).
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
        """Hook for subclasses to preprocess audio (e.g., resample)."""
        return audio_data

    # =========================================================================
    # Response Control
    # =========================================================================

    async def create_response(self) -> None:
        """Trigger model to generate a response (for non-VAD mode)."""
        # Default: no-op, override in subclasses

    async def cancel_response(self) -> None:
        """Cancel the current response."""
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
        """Send tool execution result back to the model."""
        if not self._websocket:
            raise RuntimeError("Model not started")

        # Convert result to string
        if isinstance(result, (dict, list)):
            import json

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
        """Check if connected."""
        return (
            self._websocket is not None
            and self._websocket.state.name == "OPEN"
            and self._initialized
        )

    async def close(self) -> None:
        """Close the model connection."""
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
        """Reset state for new response."""
        self.is_responding = False
        self.response_cancelled = False
