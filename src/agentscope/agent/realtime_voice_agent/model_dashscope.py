# -*- coding: utf-8 -*-
# pylint: disable=too-many-return-statements, too-many-branches
"""DashScope WebSocket-based real-time voice model with callback pattern.

This implementation uses the callback-based architecture where:
- API messages are parsed to ModelEvents
- ModelEvents are emitted via callback to Agent
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
    ModelInputTranscriptionDone,
    ModelInputStarted,
    ModelInputDone,
    ModelError,
)


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
    - Image input support (JPEG)

    .. seealso::
        - `DashScope Realtime API
          <https://help.aliyun.com/zh/model-studio/developer-reference/qwen-omni-realtime-api-websocket>`_
        - `Supported models
          <https://help.aliyun.com/zh/model-studio/developer-reference/qwen-omni-model-list>`_
        - `Supported voices
          <https://help.aliyun.com/zh/model-studio/developer-reference/cosyvoice-audio-generation-text-to-speech>`_

    Example:
        .. code-block:: python

            model = DashScopeRealtimeModel(
                api_key="your-api-key",
                model_name="qwen3-omni-flash-realtime",
                voice="Cherry",
            )

            def on_event(event: ModelEvent):
                print(f"Event: {event.type}")

            model.agent_callback = on_event
            await model.start()
    """

    WEBSOCKET_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

    def __init__(
        self,
        api_key: str,
        model_name: str = "qwen3-omni-flash-realtime",
        voice: Literal["Cherry", "Serena", "Ethan", "Chelsie"]
        | str = "Cherry",
        instructions: str = "You are a helpful assistant.",
        vad_enabled: bool = True,
        vad_threshold: float = 0.5,
        vad_silence_duration_ms: int = 800,
        enable_input_audio_transcription: bool = True,
        input_audio_format: str = "pcm",
        input_sample_rate: int = 16000,
        output_audio_format: str = "pcm",
        output_sample_rate: int = 24000,
        base_url: str | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the DashScope callback model.

        Args:
            api_key (`str`):
                The DashScope API key.
            model_name (`str`, optional):
                The model name. Defaults to "qwen3-omni-flash-realtime".
                See the `official document <>`_ for more options.
            voice (`Literal["Cherry", "Serena", "Ethan", "Chelsie"] | str`, \
            optional):
                The voice style. Supported voices: "Cherry", "Serena",
                "Ethan", "Chelsie". Defaults to "Cherry". See the
                 `official document
                 <https://help.aliyun.com/zh/model-studio/realtime>`_
                for more options.
            instructions (`str`, optional):
                The system instructions. Defaults to
                "You are a helpful assistant.".
            vad_enabled (`bool`, optional):
                Whether to enable VAD. Defaults to True.
            vad_threshold (`float`, optional):
                VAD sensitivity. Lower values make VAD more sensitive.
                Range: [-1.0, 1.0]. Defaults to 0.5.
            vad_silence_duration_ms (`int`, optional):
                Minimum silence duration after speech to trigger model
                response. Higher values wait longer before responding.
                Range: [200, 6000]. Defaults to 800.
            enable_input_audio_transcription (`bool`, optional):
                Whether to transcribe input audio. Defaults to True.
            input_audio_format (`str`, optional):
                The input audio format. Defaults to "pcm".
            input_sample_rate (`int`, optional):
                The input sample rate in Hz. Defaults to 16000.
            output_audio_format (`str`, optional):
                The output audio format. Defaults to "pcm".
            output_sample_rate (`int`, optional):
                The output sample rate in Hz. Defaults to 24000.
            base_url (`str`, optional):
                Custom WebSocket URL. Defaults to None (uses official
                DashScope endpoint).
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
        self.vad_threshold = vad_threshold
        self.vad_silence_duration_ms = vad_silence_duration_ms
        self.enable_input_audio_transcription = (
            enable_input_audio_transcription
        )
        self.input_audio_format = input_audio_format
        self.input_sample_rate = input_sample_rate
        self.output_audio_format = output_audio_format
        self.output_sample_rate = output_sample_rate
        self.base_url = base_url or self.WEBSOCKET_URL
        self.generate_kwargs = generate_kwargs or {}

        # Track current response/item IDs
        self._current_response_id: str | None = None
        self._current_item_id: str | None = None

    @property
    def provider_name(self) -> str:
        """Get the provider name.

        Returns:
            `str`:
                The provider name "dashscope".
        """
        return "dashscope"

    def _get_websocket_url(self) -> str:
        """Get DashScope WebSocket URL.

        Returns:
            `str`:
                The WebSocket URL.
        """
        return f"{self.base_url}?model={self.model_name}"

    def _get_headers(self) -> dict[str, str]:
        """Get DashScope authentication headers.

        Returns:
            `dict[str, str]`:
                The authentication headers.
        """
        return {
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-DataInspection": "disable",
        }

    def _build_session_config(self, **kwargs: Any) -> str:
        """Build DashScope session configuration message.

        Args:
            **kwargs:
                Additional configuration parameters.

        Returns:
            `str`:
                The session configuration JSON message.
        """
        session_config: dict[str, Any] = {
            "modalities": ["audio", "text"],
            "input_audio_format": self.input_audio_format,
            "output_audio_format": self.output_audio_format,
            "voice": self.voice,
            "instructions": kwargs.get("instructions", self.instructions),
            **self.generate_kwargs,
        }

        # Input audio transcription
        if self.enable_input_audio_transcription:
            session_config["input_audio_transcription"] = {
                "model": "gummy-realtime-v1",
            }

        if self.vad_enabled:
            session_config["turn_detection"] = {
                "type": "server_vad",
                "threshold": self.vad_threshold,
                "silence_duration_ms": self.vad_silence_duration_ms,
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
        """Format audio data for DashScope.

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
        """Format tool result message for DashScope.

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

    def _format_image_message(
        self,
        image_b64: str,
        mime_type: str = "image/jpeg",
    ) -> str | None:
        """Format image data for DashScope.

        DashScope Realtime API supports image input via
        input_image_buffer.append.

        Args:
            image_b64 (`str`):
                The base64 encoded image data.
            mime_type (`str`):
                The MIME type of the image. Defaults to "image/jpeg".

        Returns:
            `str | None`:
                The formatted JSON message.

        .. note::
            - Image format: JPEG recommended, 480P or 720P, max 1080P.
            - Single image should not exceed 500KB.
            - Recommended frequency: 1 image per second.
            - Must send audio data before sending images.
        """
        # DashScope currently only supports JPEG
        if mime_type not in ("image/jpeg", "image/jpg"):
            logger.warning(
                "DashScope only supports JPEG images, got %s",
                mime_type,
            )
        return json.dumps(
            {
                "type": "input_image_buffer.append",
                "image": image_b64,
            },
        )

    # pylint: disable=useless-return
    def _format_text_message(self, text: str) -> str | None:
        """Format text input for DashScope.

        Args:
            text (`str`):
                The text message to send.

        Returns:
            `str | None`:
                None, as DashScope Realtime API does not support text input.
        """
        logger.warning("DashScope Realtime API does not support text input")
        return None

    def _format_session_update_message(
        self,
        config: dict[str, Any],
    ) -> str | None:
        """Format session update message for DashScope.

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
        # Supported fields: voice, instructions, turn_detection, tools,
        # input_audio_format, output_audio_format

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

    @property
    def supports_image(self) -> bool:
        """Check if the model supports image input.

        Returns:
            `bool`:
                True, DashScope Realtime API supports image input.
        """
        return True

    def _preprocess_audio(
        self,
        audio_data: bytes,
        sample_rate: int | None,
    ) -> bytes:
        """Resample audio if needed.

        Args:
            audio_data (`bytes`):
                The raw audio data.
            sample_rate (`int`, optional):
                The sample rate of the audio.

        Returns:
            `bytes`:
                The preprocessed audio data.
        """
        if sample_rate and sample_rate != self.input_sample_rate:
            return resample_audio(
                audio_data,
                sample_rate,
                self.input_sample_rate,
            )
        return audio_data

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
        """Parse DashScope server message to ModelEvent.

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
