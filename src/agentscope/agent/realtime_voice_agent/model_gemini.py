# -*- coding: utf-8 -*-
# pylint: disable=too-many-return-statements, too-many-branches
"""Google Gemini Multimodal Live API real-time voice model with callback
pattern.

This implementation uses the callback-based architecture where:
- API messages are parsed to ModelEvents
- ModelEvents are emitted via callback to Agent

Gemini Multimodal Live API:
- WebSocket endpoint for real-time bidirectional communication
- Supports audio, text, and image input/output
- Server-side VAD (Voice Activity Detection)
- Function calling support
- Session resumption support
- Thinking/reasoning support

Reference:
    https://ai.google.dev/api/multimodal-live
"""

import json
from typing import Any, Literal

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
    ModelResponseAudioTranscriptDelta,
    ModelResponseToolUseDelta,
    ModelResponseToolUseDone,
    ModelResponseDone,
    ModelInputTranscriptionDone,
    ModelError,
)


class GeminiRealtimeModel(RealtimeVoiceModelBase):
    """Google Gemini Multimodal Live API real-time voice model.

    This model:
    - Connects to Gemini Live API via WebSocket
    - Parses API messages to unified ModelEvents
    - Emits ModelEvents via callback to Agent

    Features:
    - PCM audio input (16kHz) / output (24kHz)
    - Server-side VAD (Voice Activity Detection)
    - Image input support (multimodal)
    - Function calling support
    - Session resumption
    - Thinking/reasoning support

    .. seealso::
        - `Gemini WebSockets API reference
          <https://ai.google.dev/api/live>`_

    Example:
        .. code-block:: python

            model = GeminiRealtimeModel(
                api_key="your-api-key",
                model_name="gemini-2.5-flash-native-audio-preview-12-2025",
                voice="Puck",
            )

            def on_event(event: ModelEvent):
                print(f"Event: {event.type}")

            model.agent_callback = on_event
            await model.start()

    .. note::
        Gemini Live API uses a different message format than OpenAI/DashScope.
        Key differences:

        - Setup message is sent first to configure the session
        - Audio is sent via "realtimeInput" messages
        - Responses come as "serverContent" messages
    """

    # Gemini Multimodal Live API WebSocket endpoint (v1beta)
    WEBSOCKET_URL = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService."
        "BidiGenerateContent"
    )

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.5-flash-native-audio-preview-12-2025",
        voice: Literal["Puck", "Charon", "Kore", "Fenrir", "Aoede"]
        | str = "Puck",
        instructions: str = "You are a helpful assistant.",
        response_modalities: list[str] | None = None,
        enable_thinking: bool = False,
        thinking_budget: int | None = None,
        session_resumption: bool = False,
        session_resumption_handle: str | None = None,
        vad_enabled: bool = True,
        enable_input_audio_transcription: bool = True,
        enable_output_audio_transcription: bool = True,
        base_url: str | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
    ) -> None:
        """Initialize the Gemini Live model.

        Args:
            api_key (`str`):
                The Google API key.
            model_name (`str`, optional):
                The model name. Defaults to
                "gemini-2.5-flash-native-audio-preview-12-2025".
            voice (`Literal["Puck", "Charon", "Kore", "Fenrir", "Aoede"] | \
            str`, optional):
                The voice style. Supported voices: "Puck", "Charon",
                "Kore", "Fenrir", "Aoede". See `Gemini voices
                <https://ai.google.dev/gemini-api/docs/speech-generation#voices>`_
                for more options. Defaults to "Puck".
            instructions (`str`, optional):
                The system instructions. Defaults to
                "You are a helpful assistant.".
            response_modalities (`list[str]`, optional):
                The response modalities. Options: "TEXT", "AUDIO".
                Defaults to ["AUDIO"].
            enable_thinking (`bool`, optional):
                Whether to enable thinking/reasoning. Defaults to False.
            thinking_budget (`int`, optional):
                The token budget for thinking. Defaults to None.
            session_resumption (`bool`, optional):
                Whether to enable session resumption. Defaults to False.
            session_resumption_handle (`str`, optional):
                The previous session handle for resumption. Defaults to None.
            vad_enabled (`bool`, optional):
                Whether VAD is enabled. Defaults to True.
            enable_input_audio_transcription (`bool`, optional):
                Whether to transcribe input audio. Defaults to True.
            enable_output_audio_transcription (`bool`, optional):
                Whether to transcribe output audio. Defaults to True.
            base_url (`str`, optional):
                The custom WebSocket URL. Defaults to None.
            generate_kwargs (`dict[str, JSONSerializableObject]`, optional):
                Additional generation parameters. Defaults to None.
        """
        super().__init__(
            api_key=api_key,
            model_name=model_name,
            voice=voice,
            instructions=instructions,
        )
        self.base_url = base_url or self.WEBSOCKET_URL
        self.response_modalities = response_modalities or ["AUDIO"]
        self.enable_thinking = enable_thinking
        self.thinking_budget = thinking_budget
        self.session_resumption = session_resumption
        self.session_resumption_handle = session_resumption_handle
        self.vad_enabled = vad_enabled
        self.enable_input_audio_transcription = (
            enable_input_audio_transcription
        )
        self.enable_output_audio_transcription = (
            enable_output_audio_transcription
        )
        self.generate_kwargs = generate_kwargs or {}

        # Track current response state
        self._current_response_id: str | None = None
        self._session_id: str | None = None
        self._is_in_response = False
        self._response_counter = (
            0  # Counter for generating stable response IDs
        )

    @property
    def provider_name(self) -> str:
        """Get the provider name.

        Returns:
            `str`:
                The provider name "gemini".
        """
        return "gemini"

    @property
    def supports_image(self) -> bool:
        """Check if the model supports image input.

        Returns:
            `bool`:
                True, Gemini Live API supports image input.
        """
        return True

    def _get_websocket_url(self) -> str:
        """Get Gemini WebSocket URL with API key.

        Returns:
            `str`:
                The WebSocket URL.
        """
        return f"{self.base_url}?key={self.api_key}"

    def _get_headers(self) -> dict[str, str]:
        """Get headers for Gemini WebSocket connection.

        Returns:
            `dict[str, str]`:
                The HTTP headers.
        """
        return {
            "Content-Type": "application/json",
        }

    def _build_session_config(self, **kwargs: Any) -> str:
        """Build Gemini setup message.

        Gemini Live API requires a "setup" message as the first message
        to configure the session.

        Args:
            **kwargs:
                Additional configuration parameters.

        Returns:
            `str`:
                The setup message JSON.
        """
        setup: dict[str, Any] = {}

        # Model configuration
        setup["model"] = f"models/{self.model_name}"

        # Audio transcription configuration
        if self.enable_input_audio_transcription:
            setup["inputAudioTranscription"] = {}
        if self.enable_output_audio_transcription:
            setup["outputAudioTranscription"] = {}

        # Generation configuration
        generation_config: dict[str, Any] = {
            "responseModalities": self.response_modalities,
            **self.generate_kwargs,
        }

        # Voice configuration
        if self.voice:
            generation_config["speechConfig"] = {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": self.voice},
                },
            }

        # Thinking configuration (only if enabled)
        if self.enable_thinking:
            thinking_config: dict[str, Any] = {"includeThoughts": True}
            if self.thinking_budget:
                thinking_config["thinkingBudget"] = self.thinking_budget
            generation_config["thinkingConfig"] = thinking_config
        # Don't set thinkingConfig if not enabled - some models don't
        # support it

        setup["generationConfig"] = generation_config

        # System instruction
        instructions = kwargs.get("instructions") or self.instructions
        if instructions:
            setup["systemInstruction"] = {
                "parts": [{"text": instructions}],
            }

        # Session resumption (only if enabled with a valid handle)
        if self.session_resumption and self.session_resumption_handle:
            setup["sessionResumption"] = {
                "handle": self.session_resumption_handle,
            }

        # VAD configuration - use realtimeInputConfig
        # automaticActivityDetection is enabled by default, only set if
        # disabling
        if not self.vad_enabled:
            setup["realtimeInputConfig"] = {
                "automaticActivityDetection": {"disabled": True},
            }

        # Tools configuration
        tools = kwargs.get("tools", [])
        if tools:
            setup["tools"] = self._format_toolkit_schema(tools)

        setup_msg = json.dumps({"setup": setup})
        logger.info("Gemini setup message: %s", setup_msg[:500])
        return setup_msg

    def _format_toolkit_schema(
        self,
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Format the tools JSON schema into Gemini format.

        Args:
            schemas (`list[dict[str, Any]]`):
                The tool schemas.

        Returns:
            `list[dict[str, Any]]`:
                The formatted tools for Gemini.
        """
        function_declarations = []
        for schema in schemas:
            if "function" not in schema:
                continue
            func = schema["function"].copy()
            function_declarations.append(func)

        return [{"function_declarations": function_declarations}]

    def _format_audio_message(self, audio_b64: str) -> str:
        """Format audio data for Gemini Live API.

        Gemini uses "realtimeInput" messages with audio field.

        Args:
            audio_b64 (`str`):
                The base64 encoded audio data.

        Returns:
            `str`:
                The formatted JSON message.
        """
        return json.dumps(
            {
                "realtimeInput": {
                    "audio": {
                        "mimeType": "audio/pcm;rate=16000",
                        "data": audio_b64,
                    },
                },
            },
        )

    def _format_image_message(
        self,
        image_b64: str,
        mime_type: str = "image/jpeg",
    ) -> str | None:
        """Format image data for Gemini Live API.

        Gemini supports image input via realtimeInput with image mime type.

        Args:
            image_b64 (`str`):
                The base64 encoded image data.
            mime_type (`str`):
                The MIME type of the image. Defaults to "image/jpeg".
                Supported: "image/jpeg", "image/png", "image/webp".

        Returns:
            `str | None`:
                The formatted JSON message.

        .. note::
            - Image format: JPEG recommended, 480P or 720P, max 1080P.
            - Single image should not exceed 500KB.
            - Recommended frequency: 1 image per second.
        """
        return json.dumps(
            {
                "realtimeInput": {
                    "video": {
                        "mimeType": mime_type,
                        "data": image_b64,
                    },
                },
            },
        )

    def _format_text_message(self, text: str) -> str | None:
        """Format text input for Gemini Live API.

        Gemini supports text input via clientContent message.

        Args:
            text (`str`):
                The text message to send.

        Returns:
            `str | None`:
                The formatted JSON message.
        """
        return json.dumps(
            {
                "clientContent": {
                    "turns": [
                        {
                            "role": "user",
                            "parts": [{"text": text}],
                        },
                    ],
                    "turnComplete": True,
                },
            },
        )

    # pylint: disable=useless-return
    def _format_session_update_message(
        self,
        config: dict[str, Any],
    ) -> str | None:
        """Format session update message for Gemini.

        Gemini Live API doesn't support dynamic session update after setup.
        The session must be configured at connection time.

        Args:
            config (`dict[str, Any]`):
                The session configuration to update.

        Returns:
            `str | None`:
                None, as Gemini doesn't support dynamic session update.

        .. note::
            Gemini Live API requires session configuration to be sent
            in the initial `setup` message. Dynamic updates after
            connection are not supported.
        """
        logger.warning(
            "Gemini Live API does not support dynamic session update. "
            "Session must be configured at connection time.",
        )
        return None

    def _format_cancel_message(self) -> str | None:
        """Format cancel/interrupt message for Gemini.

        Gemini Live API doesn't have a direct cancel message.
        Sending a clientContent with turnComplete=true will interrupt
        the current response and signal end of user turn.

        Returns:
            `str | None`:
                The interrupt message JSON.

        .. note::
            Gemini requires the `turns` field in clientContent, even if empty.
            Without it, the API will return an "invalid argument" error.
        """
        # Send a turnComplete signal to interrupt current response
        # This tells Gemini the user has finished their turn
        # Note: "turns" must be present (empty array) for valid clientContent
        self._is_in_response = False
        return json.dumps(
            {
                "clientContent": {
                    "turns": [
                        {
                            "role": "user",
                            "parts": [{"text": ""}],
                        },
                    ],
                    "turnComplete": True,
                },
            },
        )

    def _format_tool_result_message(
        self,
        tool_id: str,
        tool_name: str,
        result: str,
    ) -> str:
        """Format tool result message for Gemini Live API.

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
        # Parse result if it's JSON
        try:
            result_obj = (
                json.loads(result)
                if result.startswith("{")
                else {
                    "result": result,
                }
            )
        except json.JSONDecodeError:
            result_obj = {"result": result}

        return json.dumps(
            {
                "toolResponse": {
                    "functionResponses": [
                        {
                            "id": tool_id,
                            "name": tool_name,
                            "response": result_obj,
                        },
                    ],
                },
            },
        )

    async def create_response(self, prompt: str = " ") -> None:
        """Trigger model to generate a response.

        In Gemini Live API, sending a clientContent message with
        turnComplete=true triggers response generation.

        Args:
            prompt (`str`, optional):
                The text prompt to send with the turn. Defaults to " ".

        Raises:
            RuntimeError:
                If the model is not started.
        """
        if not self._websocket:
            raise RuntimeError("Not started")

        content: dict[str, Any] = {
            "turnComplete": True,
        }

        # If prompt is provided, include it as a user turn
        if prompt:
            content["turns"] = [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                },
            ]

        message = json.dumps({"clientContent": content})
        logger.debug("Sending clientContent: %s", message)
        await self._websocket.send(message)

    def _parse_server_message(self, message: str) -> ModelEvent:
        """Parse Gemini server message to ModelEvent.

        Gemini Live API message types:
        - setupComplete: Session setup complete
        - serverContent: Model response (text/audio/transcription)
        - toolCall: Function calling request
        - toolCallCancellation: Function call cancelled
        - sessionResumptionUpdate: Session resumption handle
        - goAway: Connection closing notification
        - error: Error message

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
            logger.warning("Failed to parse Gemini message: %s", e)
            return ModelError(
                error_type="parse_error",
                code="JSON_PARSE_ERROR",
                message=f"JSON parse error: {e}",
            )

        logger.debug("Gemini message keys: %s", list(msg.keys()))

        # Handle setup complete
        if "setupComplete" in msg:
            self._session_id = "gemini_session"
            return ModelSessionCreated(session_id=self._session_id)

        # Handle server content (model response)
        if "serverContent" in msg:
            return self._parse_server_content(msg["serverContent"])

        # Handle tool call
        if "toolCall" in msg:
            return self._parse_tool_call(msg["toolCall"])

        # Handle tool call cancellation
        if "toolCallCancellation" in msg:
            logger.info("Tool call cancelled: %s", msg["toolCallCancellation"])
            self._is_in_response = False
            return ModelResponseDone(
                response_id=self._current_response_id or "",
                input_tokens=0,
                output_tokens=0,
            )

        # Handle session resumption update
        if "sessionResumptionUpdate" in msg:
            update = msg["sessionResumptionUpdate"]
            logger.info(
                "Session resumption update: handle=%s, resumable=%s",
                update.get("newHandle"),
                update.get("resumable"),
            )
            return ModelSessionUpdated(session_id=self._session_id or "")

        # Handle goAway (connection closing)
        if "goAway" in msg:
            logger.warning("Gemini connection closing: %s", msg["goAway"])
            return ModelError(
                error_type="connection_closing",
                code="GO_AWAY",
                message=str(msg["goAway"]),
            )

        # Handle error
        if "error" in msg:
            error = msg["error"]
            return ModelError(
                error_type=str(error.get("code", "unknown")),
                code=str(error.get("code", "UNKNOWN")),
                message=error.get("message", "Unknown error"),
            )

        # Unknown message type
        logger.debug("Unknown Gemini message: %s", list(msg.keys()))
        return ModelEvent(type=ModelEventType.SESSION_UPDATED)

    def _parse_server_content(self, content: dict[str, Any]) -> ModelEvent:
        """Parse serverContent message.

        serverContent contains:
        - modelTurn: Model's response content
        - turnComplete: Whether the model finished responding
        - generationComplete: Whether generation is complete
        - interrupted: Whether the response was interrupted
        - inputTranscription: User input transcription
        - outputTranscription: Model output transcription

        Args:
            content (`dict[str, Any]`):
                The serverContent message.

        Returns:
            `ModelEvent`:
                The parsed ModelEvent.
        """
        logger.debug("serverContent keys: %s", list(content.keys()))

        # Handle turn complete
        if content.get("turnComplete"):
            self._is_in_response = False
            return ModelResponseDone(
                response_id=self._current_response_id or "",
                input_tokens=0,
                output_tokens=0,
            )

        # Handle generation complete
        if content.get("generationComplete"):
            self._is_in_response = False
            return ModelResponseDone(
                response_id=self._current_response_id or "",
                input_tokens=0,
                output_tokens=0,
            )

        # Handle interrupted
        if content.get("interrupted"):
            self._is_in_response = False
            return ModelResponseDone(
                response_id=self._current_response_id or "",
                input_tokens=0,
                output_tokens=0,
            )

        # Handle input transcription (user speech to text)
        if "inputTranscription" in content:
            transcription = content["inputTranscription"]
            if transcription:
                text = transcription.get("text", "")
                logger.info("User said: %s", text)
                return ModelInputTranscriptionDone(
                    transcript=text,
                    item_id=None,
                )

        # Handle model turn content FIRST to ensure response_id is set
        if "modelTurn" in content:
            return self._parse_model_turn(content["modelTurn"])

        # Handle output transcription (model speech to text)
        # Process after modelTurn to ensure _current_response_id is set
        if "outputTranscription" in content:
            transcription = content["outputTranscription"]
            if transcription:
                text = transcription.get("text", "")
                if text:
                    logger.info("Gemini output transcription: %s", text)
                    # Ensure we're in a response before emitting transcript
                    self._ensure_response_started()
                    return ModelResponseAudioTranscriptDelta(
                        response_id=self._current_response_id or "",
                        delta=text,
                    )

        return ModelEvent(type=ModelEventType.SESSION_UPDATED)

    def _ensure_response_started(self) -> None:
        """Ensure a response is started, creating one if needed.

        This is called before emitting any response-related events to ensure
        _current_response_id is set and ResponseCreated event is emitted.
        """
        if not self._is_in_response:
            self._is_in_response = True
            self._response_counter += 1
            self._current_response_id = f"resp_gemini_{self._response_counter}"
            self._emit_event(
                ModelResponseCreated(
                    response_id=self._current_response_id,
                ),
            )

    def _parse_model_turn(self, model_turn: dict[str, Any]) -> ModelEvent:
        """Parse modelTurn message.

        .. note::
            Gemini may return multiple parts including 'thought' parts
            which contain internal reasoning. We process thought parts as
            normal content since they may contain the actual response.

        Args:
            model_turn (`dict[str, Any]`):
                The modelTurn message.

        Returns:
            `ModelEvent`:
                The parsed ModelEvent.
        """
        parts = model_turn.get("parts", [])
        if not parts:
            return ModelEvent(type=ModelEventType.SESSION_UPDATED)

        logger.debug("modelTurn parts count: %d", len(parts))

        # Start response if not already
        self._ensure_response_started()

        # Process parts - return audio first, collect text
        audio_event = None
        text_parts = []

        for part in parts:
            is_thought = part.get("thought", False)
            logger.debug(
                "Part keys: %s, thought=%s",
                list(part.keys()),
                is_thought,
            )

            # Skip thought parts - they contain internal reasoning
            if is_thought:
                continue

            # Handle inline audio data
            if "inlineData" in part:
                inline_data = part["inlineData"]
                audio_data = inline_data.get("data", "")
                audio_event = ModelResponseAudioDelta(
                    response_id=self._current_response_id or "",
                    delta=audio_data,
                )

            # Handle text content
            if "text" in part:
                text = part["text"]
                logger.info(
                    "Gemini text output: %s",
                    text[:100] if len(text) > 100 else text,
                )
                text_parts.append(text)

        # Emit text first if available
        if text_parts:
            combined_text = "".join(text_parts)
            self._emit_event(
                ModelResponseAudioTranscriptDelta(
                    response_id=self._current_response_id or "",
                    delta=combined_text,
                ),
            )

        # Return audio event if available
        if audio_event:
            return audio_event

        return ModelEvent(type=ModelEventType.SESSION_UPDATED)

    def _parse_tool_call(self, tool_call: dict[str, Any]) -> ModelEvent:
        """Parse toolCall message.

        Gemini returns complete tool call info in one message, but
        Agent expects:
        1. ModelResponseToolUseDelta (with name and arguments)
        2. ModelResponseToolUseDone (just marks completion)

        So we emit delta first, then return done.

        Args:
            tool_call (`dict[str, Any]`):
                The toolCall message.

        Returns:
            `ModelEvent`:
                The parsed ModelEvent.
        """
        function_calls = tool_call.get("functionCalls", [])

        logger.info("Gemini tool call: %s", tool_call)

        if not function_calls:
            return ModelEvent(type=ModelEventType.SESSION_UPDATED)

        # Process first function call
        fc = function_calls[0]
        call_id = fc.get("id", "")
        name = fc.get("name", "")
        args = fc.get("args", {})
        args_json = json.dumps(args) if isinstance(args, dict) else str(args)

        logger.info("Tool call: %s(%s) id=%s", name, args, call_id)

        # First emit delta with name and arguments
        self._emit_event(
            ModelResponseToolUseDelta(
                response_id=self._current_response_id or "",
                call_id=call_id,
                delta=args_json,  # Full arguments as delta
                name=name,
            ),
        )

        # Then return done
        return ModelResponseToolUseDone(
            response_id=self._current_response_id or "",
            call_id=call_id,
        )
