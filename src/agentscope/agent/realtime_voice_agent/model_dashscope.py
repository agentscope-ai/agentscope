# -*- coding: utf-8 -*-
# pylint: disable=too-many-return-statements, too-many-branches
"""DashScope WebSocket-based real-time voice model with callback pattern.

This implementation uses the callback-based architecture where:
- API messages are parsed to ModelEvents
- ModelEvents are emitted via callback to Agent
"""

import json
from typing import Any

import numpy as np

from ..._logging import logger
from ...types import JSONSerializableObject

from .model import RealtimeVoiceModelBase
from .events import (
    ModelEvent,
    ModelEventType,
    ModelSessionCreated,
    ModelSessionUpdated,
    ModelResponseCreated,
    ModelResponseAudioDelta,
    ModelResponseAudioDone,
    ModelResponseAudioTranscriptDelta,
    ModelResponseAudioTranscriptDone,
    ModelResponseToolUseDelta,
    ModelResponseToolUseDone,
    ModelResponseDone,
    ModelInputTranscriptionDone,
    ModelInputStarted,
    ModelInputDone,
    ModelError,
)


def _resample_audio(
    audio_data: bytes,
    from_rate: int,
    to_rate: int,
) -> bytes:
    """Resample audio data from one sample rate to another.

    Args:
        audio_data: PCM audio bytes (16-bit signed, mono).
        from_rate: Source sample rate in Hz.
        to_rate: Target sample rate in Hz.

    Returns:
        Resampled PCM audio bytes.
    """
    if from_rate == to_rate:
        return audio_data

    # Convert bytes to numpy array (16-bit signed)
    audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)

    # Calculate the number of samples in the resampled audio
    num_samples = int(len(audio_array) * to_rate / from_rate)

    # Resample using linear interpolation
    indices = np.linspace(0, len(audio_array) - 1, num_samples)
    resampled = np.interp(indices, np.arange(len(audio_array)), audio_array)

    # Convert back to 16-bit signed integer bytes
    return resampled.astype(np.int16).tobytes()


class DashScopeRealtimeModel(RealtimeVoiceModelBase):
    """DashScope real-time voice model using callback pattern.

    This model:
    - Connects to DashScope Realtime API via WebSocket
    - Parses API messages to unified ModelEvents
    - Emits ModelEvents via callback to Agent

    Features:
    - PCM audio input (16kHz mono)
    - PCM audio output (24kHz mono)
    - Server-side VAD (Voice Activity Detection)
    - Input audio transcription

    Example:
        ```python
        model = DashScopeRealtimeModel(
            api_key="your-api-key",
            model_name="qwen3-omni-flash-realtime",
            voice="Cherry",
        )

        def on_event(event: ModelEvent):
            print(f"Event: {event.type}")

        model.agent_callback = on_event
        await model.start()
        ```
    """

    WEBSOCKET_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

    def __init__(
        self,
        api_key: str,
        model_name: str = "qwen3-omni-flash-realtime",
        voice: str = "Cherry",
        instructions: str = "You are a helpful assistant.",
        vad_enabled: bool = True,
        input_audio_format: str = "pcm",
        input_sample_rate: int = 16000,
        output_audio_format: str = "pcm",
        output_sample_rate: int = 24000,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the DashScope callback model.

        Args:
            api_key: DashScope API key.
            model_name: Model name (default: qwen3-omni-flash-realtime).
            voice: Voice style (default: Cherry).
            instructions: System instructions.
            vad_enabled: Whether to enable VAD (default: True).
            input_audio_format: Input audio format (default: pcm).
            input_sample_rate: Input sample rate in Hz (default: 16000).
            output_audio_format: Output audio format (default: pcm).
            output_sample_rate: Output sample rate in Hz (default: 24000).
            generate_kwargs: Additional generation parameters.
        """
        super().__init__(
            api_key=api_key,
            model_name=model_name,
            voice=voice,
            instructions=instructions,
        )
        self.vad_enabled = vad_enabled
        self.input_audio_format = input_audio_format
        self.input_sample_rate = input_sample_rate
        self.output_audio_format = output_audio_format
        self.output_sample_rate = output_sample_rate
        self.generate_kwargs = generate_kwargs or {}

        # Track current response/item IDs
        self._current_response_id: str | None = None
        self._current_item_id: str | None = None

    @property
    def provider_name(self) -> str:
        """Get the provider name."""
        return "dashscope"

    def _get_websocket_url(self) -> str:
        """Get DashScope WebSocket URL."""
        return f"{self.WEBSOCKET_URL}?model={self.model_name}"

    def _get_headers(self) -> dict[str, str]:
        """Get DashScope authentication headers."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-DataInspection": "disable",
        }

    def _build_session_config(self, **kwargs: Any) -> str:
        """Build DashScope session configuration message."""
        session_config: dict[str, Any] = {
            "modalities": ["audio", "text"],
            "input_audio_format": self.input_audio_format,
            "output_audio_format": self.output_audio_format,
            "voice": self.voice,
            "instructions": kwargs.get("instructions", self.instructions),
            "input_audio_transcription": {
                "model": "gummy-realtime-v1",
            },
            **self.generate_kwargs,
        }

        if self.vad_enabled:
            session_config["turn_detection"] = {
                "type": "server_vad",
            }
        else:
            session_config["turn_detection"] = None

        return json.dumps(
            {
                "type": "session.update",
                "session": session_config,
            },
        )

    def _format_audio_message(self, audio_b64: str) -> str:
        """Format audio data for DashScope."""
        return json.dumps(
            {
                "type": "input_audio_buffer.append",
                "audio": audio_b64,
            },
        )

    def _format_cancel_message(self) -> str | None:
        """Format cancel response message."""
        return json.dumps({"type": "response.cancel"})

    def _format_tool_result_message(
        self,
        tool_id: str,
        tool_name: str,
        result: str,
    ) -> str:
        """Format tool result message for DashScope."""
        return json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": tool_id,
                    "output": result,
                },
            },
        )

    def _preprocess_audio(
        self,
        audio_data: bytes,
        sample_rate: int | None,
    ) -> bytes:
        """Resample audio if needed."""
        if sample_rate and sample_rate != self.input_sample_rate:
            return _resample_audio(
                audio_data,
                sample_rate,
                self.input_sample_rate,
            )
        return audio_data

    async def create_response(self) -> None:
        """Trigger model to generate a response."""
        if not self._websocket:
            raise RuntimeError("Not started")

        response_create = json.dumps({"type": "response.create"})
        await self._websocket.send(response_create)

    def _parse_server_message(self, message: str) -> ModelEvent:
        """Parse DashScope server message to ModelEvent."""
        try:
            msg = json.loads(message)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse DashScope message: %s", e)
            return ModelError(
                error_type="parse_error",
                code="JSON_PARSE_ERROR",
                message=f"JSON parse error: {e}",
            )

        event_type = msg.get("type", "")

        # Session events
        if event_type == "session.created":
            session_id = msg.get("session", {}).get("id", "")
            return ModelSessionCreated(session_id=session_id)

        elif event_type == "session.updated":
            session_id = msg.get("session", {}).get("id", "")
            return ModelSessionUpdated(session_id=session_id)

        # Response events
        elif event_type == "response.created":
            response = msg.get("response", {})
            response_id = response.get("id", "")
            self._current_response_id = response_id
            return ModelResponseCreated(response_id=response_id)

        elif event_type == "response.done":
            response = msg.get("response", {})
            response_id = response.get("id", self._current_response_id or "")
            usage = response.get("usage", {})
            return ModelResponseDone(
                response_id=response_id,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )

        # Audio delta
        elif event_type == "response.audio.delta":
            audio_data = msg.get("delta", "")
            return ModelResponseAudioDelta(
                response_id=self._current_response_id or "",
                delta=audio_data,
                item_id=msg.get("item_id"),
                content_index=msg.get("content_index"),
                output_index=msg.get("output_index"),
            )

        elif event_type == "response.audio.done":
            return ModelResponseAudioDone(
                response_id=self._current_response_id or "",
                item_id=msg.get("item_id"),
                content_index=msg.get("content_index"),
                output_index=msg.get("output_index"),
            )

        # Transcript delta
        elif event_type == "response.audio_transcript.delta":
            text = msg.get("delta", "")
            return ModelResponseAudioTranscriptDelta(
                response_id=self._current_response_id or "",
                delta=text,
                item_id=msg.get("item_id"),
                content_index=msg.get("content_index"),
                output_index=msg.get("output_index"),
            )

        elif event_type == "response.audio_transcript.done":
            return ModelResponseAudioTranscriptDone(
                response_id=self._current_response_id or "",
                item_id=msg.get("item_id"),
                content_index=msg.get("content_index"),
                output_index=msg.get("output_index"),
            )

        # Tool use events (function calling)
        elif event_type == "response.function_call_arguments.delta":
            return ModelResponseToolUseDelta(
                response_id=self._current_response_id or "",
                call_id=msg.get("call_id", ""),
                delta=msg.get("delta", ""),
                item_id=msg.get("item_id"),
                output_index=msg.get("output_index"),
                name=msg.get("name"),
            )

        elif event_type == "response.function_call_arguments.done":
            return ModelResponseToolUseDone(
                response_id=self._current_response_id or "",
                call_id=msg.get("call_id", ""),
                item_id=msg.get("item_id"),
                output_index=msg.get("output_index"),
            )

        # Input transcription
        elif (
            event_type
            == "conversation.item.input_audio_transcription.completed"
        ):
            text = msg.get("transcript", "")
            logger.info("User said: %s", text)
            return ModelInputTranscriptionDone(
                transcript=text,
                item_id=msg.get("item_id"),
            )

        # VAD events
        elif event_type == "input_audio_buffer.speech_started":
            return ModelInputStarted(
                item_id=msg.get("item_id", ""),
                audio_start_ms=msg.get("audio_start_ms", 0),
            )

        elif event_type == "input_audio_buffer.speech_stopped":
            return ModelInputDone(
                item_id=msg.get("item_id", ""),
                audio_end_ms=msg.get("audio_end_ms", 0),
            )

        # Error events
        elif event_type == "error":
            error = msg.get("error", {})
            return ModelError(
                error_type=error.get("type", "unknown"),
                code=error.get("code", "UNKNOWN"),
                message=error.get("message", "Unknown error"),
            )

        # Unknown event - return generic event
        else:
            logger.debug("Unknown DashScope event type: %s", event_type)
            # Return a generic session event for unknown types
            return ModelEvent(type=ModelEventType.SESSION_UPDATED)
