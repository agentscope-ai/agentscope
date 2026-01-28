# -*- coding: utf-8 -*-
# pylint: disable=too-many-return-statements, too-many-branches
"""OpenAI Realtime API model with callback pattern.

This implementation uses the callback-based architecture where:
- API messages are parsed to ModelEvents
- ModelEvents are emitted via callback to Agent

OpenAI Realtime API:
- WebSocket endpoint for real-time bidirectional communication
- Supports audio and text input/output
- Server-side VAD (Voice Activity Detection)
- Function calling support

Reference:
    https://platform.openai.com/docs/api-reference/realtime
"""

import json
from typing import Any, Literal

from ..._logging import logger
from ...types import JSONSerializableObject

from .model import RealtimeVoiceModelBase, resample_audio
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
    ModelInputTranscriptionDelta,
    ModelInputTranscriptionDone,
    ModelInputStarted,
    ModelInputDone,
    ModelError,
)


class OpenAIRealtimeModel(RealtimeVoiceModelBase):
    """OpenAI Realtime API model using callback pattern.

    This model:
    - Connects to OpenAI Realtime API via WebSocket
    - Parses API messages to unified ModelEvents
    - Emits ModelEvents via callback to Agent

    Features:
    - PCM audio input (16kHz recommended, auto-resampled to 24kHz)
    - PCM audio output (24kHz mono)
    - Server-side VAD (Voice Activity Detection)
    - Input audio transcription (Whisper)
    - Function calling support

    .. seealso::
        - `OpenAI Realtime API with WebSocket
          <https://platform.openai.com/docs/guides/realtime-websocket>`_
        - `Supported models <https://platform.openai.com/docs/models>`_
        - `Supported voices
          <https://platform.openai.com/docs/api-reference/realtime-client-events/session/update#realtime_client_events-session-update-session-realtime_session_configuration-audio-output-voice>`_

    Example:
        .. code-block:: python

            model = OpenAIRealtimeModel(
                api_key="your-openai-api-key",
                model_name="gpt-4o-realtime-preview-2024-12-17",
                voice="alloy",
            )

            def on_event(event: ModelEvent):
                print(f"Event: {event.type}")

            model.agent_callback = on_event
            await model.start()
    """

    WEBSOCKET_URL = "wss://api.openai.com/v1/realtime"

    def __init__(
        self,
        api_key: str,
        model_name: str = "gpt-4o-realtime-preview-2024-12-17",
        voice: Literal[
            "alloy",
            "ash",
            "ballad",
            "coral",
            "echo",
            "marin",
            "cedar",
        ]
        | str = "marin",
        instructions: str = "You are a helpful assistant.",
        vad_enabled: bool = True,
        enable_input_audio_transcription: bool = True,
        turn_detection_threshold: float = 0.5,
        turn_detection_prefix_padding_ms: int = 300,
        turn_detection_silence_duration_ms: int = 500,
        base_url: str | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the OpenAI Realtime model.

        Args:
            api_key (`str`):
                The OpenAI API key.
            model_name (`str`, optional):
                The model name. Defaults to
                "gpt-4o-realtime-preview-2024-12-17".
            voice (`Literal["alloy", "ash", "ballad", "coral", "echo", \
            "marin", "cedar"] | str`, optional):
                The voice style. Supported voices: "alloy", "ash",
                "ballad", "coral", "echo", "marin", "cedar", etc.
                Defaults to "marin". See `OpenAI voices
                <https://platform.openai.com/docs/api-reference/realtime-client-events/session/update#realtime_client_events-session-update-session-realtime_session_configuration-audio-output-voice>`_
                for more options.
            instructions (`str`, optional):
                The system instructions. Defaults to
                "You are a helpful assistant.".
            vad_enabled (`bool`, optional):
                Whether to enable server VAD. Defaults to True.
            enable_input_audio_transcription (`bool`, optional):
                Whether to transcribe input audio. Defaults to True.
            turn_detection_threshold (`float`, optional):
                VAD threshold (0.0-1.0). Defaults to 0.5.
            turn_detection_prefix_padding_ms (`int`, optional):
                Padding before speech in ms. Defaults to 300.
            turn_detection_silence_duration_ms (`int`, optional):
                Silence duration to end turn in ms. Defaults to 500.
            base_url (`str`, optional):
                Custom WebSocket URL. Defaults to None (uses official
                OpenAI endpoint).
            generate_kwargs (`dict[str, JSONSerializableObject]`, optional):
                Additional generation parameters. Defaults to None.
        """
        super().__init__(
            api_key=api_key,
            model_name=model_name,
            voice=voice,
            instructions=instructions,
        )
        self.vad_enabled = vad_enabled
        self.enable_input_audio_transcription = (
            enable_input_audio_transcription
        )
        self.turn_detection_threshold = turn_detection_threshold
        self.turn_detection_prefix_padding_ms = (
            turn_detection_prefix_padding_ms
        )
        self.turn_detection_silence_duration_ms = (
            turn_detection_silence_duration_ms
        )
        self.base_url = base_url or self.WEBSOCKET_URL
        self.generate_kwargs = generate_kwargs or {}

        # OpenAI Realtime API expects 24kHz PCM input
        self.input_sample_rate = 24000

        # Track current response/item IDs
        self._current_response_id: str | None = None
        self._current_item_id: str | None = None

    @property
    def provider_name(self) -> str:
        """Get the provider name.

        Returns:
            `str`:
                The provider name "openai".
        """
        return "openai"

    @property
    def supports_image(self) -> bool:
        """Check if the model supports image input.

        Returns:
            `bool`:
                False, OpenAI Realtime API does not support image input yet.
        """
        return False

    def _get_websocket_url(self) -> str:
        """Get OpenAI WebSocket URL.

        Returns:
            `str`:
                The WebSocket URL with model parameter.
        """
        return f"{self.base_url}?model={self.model_name}"

    def _get_headers(self) -> dict[str, str]:
        """Get OpenAI authentication headers.

        Returns:
            `dict[str, str]`:
                The authentication headers.
        """
        return {
            "Authorization": f"Bearer {self.api_key}",
            "OpenAI-Beta": "realtime=v1",
        }

    def _build_session_config(self, **kwargs: Any) -> str:
        """Build OpenAI session configuration message.

        Args:
            **kwargs:
                Additional configuration parameters.

        Returns:
            `str`:
                The session configuration JSON message.
        """
        session_config: dict[str, Any] = {
            "modalities": ["audio", "text"],
            "voice": self.voice,
            "instructions": kwargs.get("instructions", self.instructions),
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            **self.generate_kwargs,
        }

        # Input audio transcription
        if self.enable_input_audio_transcription:
            session_config["input_audio_transcription"] = {
                "model": "whisper-1",
            }

        # Turn detection (VAD)
        if self.vad_enabled:
            session_config["turn_detection"] = {
                "type": "server_vad",
                "threshold": self.turn_detection_threshold,
                "prefix_padding_ms": self.turn_detection_prefix_padding_ms,
                "silence_duration_ms": self.turn_detection_silence_duration_ms,
            }
        else:
            session_config["turn_detection"] = None

        # Tools configuration
        tools = kwargs.get("tools", [])
        if tools:
            session_config["tools"] = tools

        return json.dumps(
            {
                "type": "session.update",
                "session": session_config,
            },
        )

    def _format_audio_message(self, audio_b64: str) -> str:
        """Format audio data for OpenAI.

        Args:
            audio_b64 (`str`):
                The base64 encoded audio data.

        Returns:
            `str`:
                The formatted JSON message.
        """
        return json.dumps(
            {
                "type": "input_audio_buffer.append",
                "audio": audio_b64,
            },
        )

    def _preprocess_audio(
        self,
        audio_data: bytes,
        sample_rate: int | None,
    ) -> bytes:
        """Resample audio to 24kHz if needed.

        OpenAI Realtime API expects 24kHz PCM input.

        Args:
            audio_data (`bytes`):
                The raw audio data.
            sample_rate (`int`, optional):
                The sample rate of the audio.

        Returns:
            `bytes`:
                The preprocessed audio data (resampled to 24kHz if needed).
        """
        if sample_rate and sample_rate != self.input_sample_rate:
            return resample_audio(
                audio_data,
                sample_rate,
                self.input_sample_rate,
            )
        return audio_data

    def _format_cancel_message(self) -> str | None:
        """Format cancel response message.

        Returns:
            `str | None`:
                The cancel message JSON.
        """
        return json.dumps({"type": "response.cancel"})

    def _format_tool_result_message(
        self,
        tool_id: str,
        tool_name: str,
        result: str,
    ) -> str:
        """Format tool result message for OpenAI.

        Args:
            tool_id (`str`):
                The tool call ID.
            tool_name (`str`):
                The tool name.
            result (`str`):
                The tool execution result.

        Returns:
            `str`:
                The formatted JSON message.
        """
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

    # pylint: disable=useless-return
    def _format_image_message(
        self,
        image_b64: str,
        mime_type: str = "image/jpeg",
    ) -> str | None:
        """Format image data for OpenAI.

        Args:
            image_b64 (`str`):
                The base64 encoded image data.
            mime_type (`str`):
                The MIME type of the image. Not used as OpenAI doesn't
                support image input.

        Returns:
            `str | None`:
                None, as OpenAI Realtime API does not support image input.
        """
        logger.warning("OpenAI Realtime API does not support image input")
        return None

    def _format_text_message(self, text: str) -> str | None:
        """Format text input for OpenAI.

        OpenAI Realtime API supports text input via conversation.item.create.

        Args:
            text (`str`):
                The text message to send.

        Returns:
            `str | None`:
                The formatted JSON message.
        """
        return json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": text,
                        },
                    ],
                },
            },
        )

    def _format_session_update_message(
        self,
        config: dict[str, Any],
    ) -> str | None:
        """Format session update message for OpenAI.

        Args:
            config (`dict[str, Any]`):
                The session configuration to update.

        Returns:
            `str | None`:
                The formatted JSON message.
        """
        # TODO: Consider passing config directly to API without field mapping.
        # Currently we filter known fields for safety, but this could be
        # simplified if the frontend sends API-compatible config directly.
        # Supported fields: voice, instructions, turn_detection, modalities,
        # tools, input_audio_format, output_audio_format

        # Pass through known fields directly
        known_fields = [
            "voice",
            "instructions",
        ]
        session_config = {k: v for k, v in config.items() if k in known_fields}

        if not session_config:
            logger.warning("No valid config keys found for session update")
            return None

        return json.dumps(
            {
                "type": "session.update",
                "session": session_config,
            },
        )

    async def create_response(self) -> None:
        """Trigger model to generate a response.

        Raises:
            RuntimeError:
                If the model is not started.
        """
        if not self._websocket:
            raise RuntimeError("Not started")

        response_create = json.dumps({"type": "response.create"})
        await self._websocket.send(response_create)

    def _parse_server_message(self, message: str) -> ModelEvent:
        """Parse OpenAI server message to ModelEvent.

        Args:
            message (`str`):
                The server message to parse.

        Returns:
            `ModelEvent`:
                The parsed ModelEvent.
        """
        try:
            msg = json.loads(message)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse OpenAI message: %s", e)
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

        # Transcript delta (model output)
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

        # Input transcription
        elif (
            event_type
            == "conversation.item.input_audio_transcription.completed"
        ):
            text = msg.get("transcript", "")
            item_id = msg.get("item_id", "")
            return ModelInputTranscriptionDone(
                transcript=text,
                item_id=item_id,
            )

        elif event_type == "conversation.item.input_audio_transcription.delta":
            text = msg.get("delta", "")
            return ModelInputTranscriptionDelta(
                delta=text,
                item_id=msg.get("item_id"),
                content_index=msg.get("content_index"),
            )

        # Input detection (VAD)
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

        # Tool use events
        elif event_type == "response.function_call_arguments.delta":
            return ModelResponseToolUseDelta(
                response_id=self._current_response_id or "",
                call_id=msg.get("call_id", ""),
                delta=msg.get("delta", ""),
                name=msg.get("name"),
            )

        elif event_type == "response.function_call_arguments.done":
            return ModelResponseToolUseDone(
                response_id=self._current_response_id or "",
                call_id=msg.get("call_id", ""),
            )

        # Error events
        elif event_type == "error":
            error = msg.get("error", {})
            return ModelError(
                error_type=error.get("type", "unknown"),
                code=error.get("code", "UNKNOWN"),
                message=error.get("message", "Unknown error"),
            )

        # Rate limit info (not an error, just informational)
        elif event_type == "rate_limits.updated":
            logger.debug("Rate limits updated: %s", msg.get("rate_limits"))
            return ModelEvent(type=ModelEventType.SESSION_UPDATED)

        # Informational events - state notifications without actual data
        # These events notify about structural changes but don't contain
        # audio/text content. The actual data comes in events like
        # response.audio.delta and response.audio_transcript.delta.
        # We return a generic ModelEvent to acknowledge receipt without
        # triggering any special handling in the Agent layer.
        elif event_type in [
            "conversation.item.created",  # New item added to conversation
            "conversation.item.deleted",  # Item removed from conversation
            "response.output_item.added",  # Output item structure created
            "response.output_item.done",  # Output item completed
            "response.content_part.added",  # Content part structure created
            "response.content_part.done",  # Content part completed
            "input_audio_buffer.committed",  # Audio buffer submitted
            "input_audio_buffer.cleared",  # Audio buffer cleared
        ]:
            logger.debug("Informational event: %s", event_type)
            return ModelEvent(type=ModelEventType.SESSION_UPDATED)

        # Unknown event type
        logger.debug("Unknown OpenAI event: %s", event_type)
        return ModelEvent(type=ModelEventType.SESSION_UPDATED)
