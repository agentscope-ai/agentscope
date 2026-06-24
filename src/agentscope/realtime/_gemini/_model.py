# -*- coding: utf-8 -*-
"""The Gemini realtime model class.

Implements the Gemini Live API (BidiGenerateContent) for bidirectional
streaming with audio, text, image, and tool-call support.
See: https://ai.google.dev/gemini-api/docs/live
"""
import asyncio
import json
from typing import Any, Literal

import shortuuid
from pydantic import Field

from .._base import RealtimeModelBase
from .._events import AudioFormat, ModelEvents
from ..._logging import logger
from ...credential import GeminiCredential
from ...message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)


_GEMINI_WS_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService."
    "BidiGenerateContent?key={api_key}"
)


class GeminiRealtimeModel(RealtimeModelBase):
    """A bidirectional realtime client for the Gemini Live API.

    Example:

        .. code-block:: python

            from asyncio import Queue
            from agentscope.credential import GeminiCredential
            from agentscope.realtime import GeminiRealtimeModel

            model = GeminiRealtimeModel(
                model_name="gemini-2.5-flash-native-audio-preview-12-2025",
                credential=GeminiCredential(api_key=...),
            )
            queue: Queue = Queue()
            await model.connect(queue, instructions="You are a helpful agent.")
            await model.send(audio_data_block)  # base64 PCM 16kHz mono
            event = await queue.get()           # ModelEvents.* instance
            await model.disconnect()

    .. note::
        - Tools are supported via the Gemini function-calling protocol.
        - The Gemini Live API does not send explicit ``response.created``
          events; response IDs are generated client-side.
        - Input audio is 16 kHz mono PCM; output audio is 24 kHz mono PCM.
    """

    class Parameters(RealtimeModelBase.Parameters):
        """Frontend-exposed parameters for Gemini realtime models."""

        voice: str = Field(
            default="Puck",
            title="Voice",
            description="The TTS voice for spoken responses.",
        )

        enable_input_audio_transcription: bool = Field(
            default=True,
            title="Input Audio Transcription",
            description="Whether to transcribe the user's input audio.",
        )

        enable_context_compression: bool = Field(
            default=True,
            title="Context Window Compression",
            description=(
                "Enable sliding-window compression to extend session "
                "duration beyond the default 15-minute audio / 2-minute "
                "video limit.  When enabled, the server automatically "
                "prunes the oldest turns when the context exceeds the "
                "trigger threshold."
            ),
        )

        context_compression_trigger_tokens: int | None = Field(
            default=None,
            title="Compression Trigger Tokens",
            description=(
                "Number of tokens that triggers compression. "
                "Default (None) = 80% of the model's context window limit. "
                "Range: 5000–128000."
            ),
        )

        context_compression_target_tokens: int | None = Field(
            default=None,
            title="Compression Target Tokens",
            description=(
                "Target number of tokens to retain after compression. "
                "Default (None) = 50% of trigger_tokens. "
                "Range: 0–128000."
            ),
        )

    type: Literal["gemini_realtime"] = "gemini_realtime"
    """The type of the realtime model."""

    support_input_modalities: list[str] = [
        "audio",
        "text",
        "image",
        "tool_result",
    ]
    """The Gemini Live API accepts audio, text, image, and tool results."""

    support_tools: bool = True
    """The Gemini Live API supports function-call tools."""

    def __init__(
        self,
        model_name: str,
        credential: GeminiCredential,
        parameters: "GeminiRealtimeModel.Parameters | None" = None,
    ) -> None:
        """Initialize the Gemini realtime model.

        Args:
            model_name (`str`):
                The Gemini realtime model, e.g.
                ``"gemini-2.5-flash-native-audio-preview-12-2025"``.
            credential (`GeminiCredential`):
                The Gemini credential containing the API key.
            parameters (`GeminiRealtimeModel.Parameters | None`, defaults \
            to `None`):
                The realtime model parameters. When ``None``, the default
                parameters will be used.
        """
        super().__init__(model_name)

        self.credential = credential
        self.parameters = parameters or self.Parameters()

        self.voice = self.parameters.voice
        self.enable_input_audio_transcription = (
            self.parameters.enable_input_audio_transcription
        )

        # Gemini Live API: 16 kHz input, 24 kHz output
        self.input_sample_rate = 16000
        self.output_sample_rate = 24000

        self.websocket_url = _GEMINI_WS_URL.format(
            api_key=credential.api_key.get_secret_value(),
        )
        self.websocket_headers = {
            "Content-Type": "application/json",
        }

        # Unlike OpenAI/DashScope which send explicit ``response.created``,
        # Gemini does not. We generate response IDs ourselves.
        self._response_id: str | None = None

        # Session resumption: latest handle received from the server.
        self._session_handle: str | None = None

    def _ensure_response_id(self) -> str:
        """Get the current response ID, creating one if needed."""
        if not self._response_id:
            self._response_id = shortuuid.uuid()
        assert self._response_id is not None
        return self._response_id

    # ------------------------------------------------------------------
    # Session config
    # ------------------------------------------------------------------

    def _build_session_config(
        self,
        instructions: str,
        tools: list[dict] | None,
        **kwargs: Any,
    ) -> dict:
        """Build the Gemini ``setup`` message.

        The Gemini Live API requires a ``setup`` message as the first
        message to configure the session.

        Args:
            instructions (`str`):
                System instructions for the model.
            tools (`list[dict] | None`):
                Tool JSON schemas.
            **kwargs (`Any`):
                Extra fields.  ``session_handle`` (if present) enables
                session resumption with a previously obtained handle.

        Returns:
            `dict`: The ``setup`` message.
        """
        session_handle = kwargs.pop("session_handle", None)

        session_config: dict[str, Any] = {
            "model": f"models/{self.model_name}",
            "systemInstruction": {
                "parts": [{"text": instructions}],
            },
            "outputAudioTranscription": {},
        }

        if self.enable_input_audio_transcription:
            session_config["inputAudioTranscription"] = {}

        generation_config: dict[str, Any] = {
            "responseModalities": ["AUDIO"],
            **kwargs,
        }

        if self.voice:
            generation_config["speechConfig"] = {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": self.voice},
                },
            }

        # Context window compression: enables sessions longer than the
        # default 15-min audio / 2-min video limit by auto-pruning old turns.
        # NOTE: contextWindowCompression is a top-level field in the setup
        # message, NOT nested inside generationConfig.
        if self.parameters.enable_context_compression:
            sliding_window: dict[str, Any] = {}
            if self.parameters.context_compression_target_tokens is not None:
                sliding_window[
                    "targetTokens"
                ] = self.parameters.context_compression_target_tokens
            compression: dict[str, Any] = {"slidingWindow": sliding_window}
            if self.parameters.context_compression_trigger_tokens is not None:
                compression[
                    "triggerTokens"
                ] = self.parameters.context_compression_trigger_tokens
            session_config["contextWindowCompression"] = compression

        session_config["generationConfig"] = generation_config

        if tools:
            session_config["tools"] = self._format_toolkit_schema(tools)

        # Session resumption: pass a previous handle to restore context.
        resumption_config: dict[str, Any] = {}
        if session_handle:
            resumption_config["handle"] = session_handle
        session_config["sessionResumption"] = resumption_config

        return {"setup": session_config}

    @staticmethod
    def _format_toolkit_schema(
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Format agentscope tool schemas into Gemini format.

        Args:
            schemas (`list[dict[str, Any]]`):
                Tool schemas as produced by
                :meth:`agentscope.tool.Toolkit.get_tool_schemas`.

        Returns:
            `list[dict[str, Any]]`:
                Tool schemas in Gemini ``function_declarations`` format.
        """
        function_declarations = []
        for schema in schemas:
            if "function" not in schema:
                continue
            function_declarations.append(schema["function"].copy())
        return [{"function_declarations": function_declarations}]

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send(
        self,
        data: DataBlock | TextBlock | ToolResultBlock,
    ) -> None:
        """Send a content block to the Gemini realtime model.

        Args:
            data (`DataBlock | TextBlock | ToolResultBlock`):
                The block to send. ``DataBlock`` carries audio or image
                (discriminated by ``media_type``). Text is sent as a
                ``clientContent`` turn. Tool results use the
                ``toolResponse`` protocol.
        """
        from websockets import State

        if not self._websocket or self._websocket.state != State.OPEN:
            raise RuntimeError(
                f"WebSocket is not connected for model {self.model_name}. "
                "Call `connect` first.",
            )

        payload: str | None = None

        if isinstance(data, DataBlock):
            media_type = data.source.media_type
            major = media_type.split("/", 1)[0]
            if major == "audio":
                data = self._resample_audio_if_needed(data)
                payload = self._encode_audio(data)
            elif major == "image":
                if data.source.type == "base64":
                    payload = self._encode_image(data)
                else:
                    payload = await asyncio.to_thread(
                        self._encode_image,
                        data,
                    )
            else:
                logger.warning(
                    "GeminiRealtimeModel: unsupported DataBlock "
                    "media_type %r; skipping.",
                    media_type,
                )

        elif isinstance(data, TextBlock):
            payload = self._encode_text(data)

        elif isinstance(data, ToolResultBlock):
            payload = self._encode_tool_result(data)

        else:
            raise TypeError(
                f"GeminiRealtimeModel.send: unsupported block type "
                f"{type(data).__name__}.",
            )

        if payload is not None:
            await self._websocket.send(payload)

    @staticmethod
    def _encode_audio(block: DataBlock) -> str:
        """Encode an audio ``DataBlock`` as a Gemini ``realtimeInput``.

        The Gemini Live API requires the MIME type to include the sample
        rate (e.g. ``audio/pcm;rate=16000``).  The rate is read from the
        block's own ``media_type`` so that any upstream resampling is
        correctly reflected.
        """
        if not isinstance(block.source, Base64Source):
            raise ValueError(
                "GeminiRealtimeModel: audio DataBlock must use Base64Source.",
            )
        return json.dumps(
            {
                "realtimeInput": {
                    "audio": {
                        "mimeType": block.source.media_type,
                        "data": block.source.data,
                    },
                },
            },
        )

    @staticmethod
    def _encode_image(block: DataBlock) -> str:
        """Encode an image ``DataBlock`` as a Gemini ``realtimeInput``.

        The Gemini Live API requires inline data for realtime input.
        If the source is a URL (file:// or http(s)://), the image is
        fetched/read and base64-encoded automatically.
        """
        import base64 as b64_mod

        if isinstance(block.source, Base64Source):
            media_type = block.source.media_type
            data = block.source.data
        else:
            media_type = block.source.media_type
            url_str = str(block.source.url)
            if url_str.startswith("file://"):
                local_path = url_str.removeprefix("file://")
                with open(local_path, "rb") as f:
                    data = b64_mod.b64encode(f.read()).decode("utf-8")
            else:
                import requests

                resp = requests.get(url_str, timeout=30)
                resp.raise_for_status()
                data = b64_mod.b64encode(resp.content).decode("utf-8")

        return json.dumps(
            {
                "realtimeInput": {
                    "video": {
                        "mimeType": media_type,
                        "data": data,
                    },
                },
            },
        )

    @staticmethod
    def _encode_text(block: TextBlock) -> str:
        """Encode a ``TextBlock`` as a Gemini ``realtimeInput.text``.

        All real-time user input (audio, video, text) must use
        ``realtimeInput`` — ``clientContent`` is reserved for seeding
        initial context history only.
        """
        return json.dumps(
            {
                "realtimeInput": {
                    "text": block.text,
                },
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _encode_tool_result(block: ToolResultBlock) -> str:
        """Encode a ``ToolResultBlock`` as a Gemini ``toolResponse``.

        The Gemini ``functionResponses`` payload expects a dict.  If the
        output is a plain string it is wrapped as ``{"result": <text>}``;
        if it is a list of content blocks, only ``TextBlock`` entries are
        extracted and concatenated.
        """
        if isinstance(block.output, str):
            try:
                result_obj = json.loads(block.output)
            except json.JSONDecodeError:
                result_obj = {"result": block.output}
        else:
            parts: list[str] = []
            for entry in block.output:
                if isinstance(entry, TextBlock):
                    parts.append(entry.text)
                else:
                    logger.debug(
                        "GeminiRealtimeModel: dropping non-text tool "
                        "result block of type %r in toolResponse.",
                        type(entry).__name__,
                    )
            result_obj = {"result": "".join(parts)}

        return json.dumps(
            {
                "toolResponse": {
                    "functionResponses": [
                        {
                            "id": block.id,
                            "name": block.name,
                            "response": result_obj,
                        },
                    ],
                },
            },
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------
    # Incoming frame parsing
    # ------------------------------------------------------------------

    # pylint: disable=too-many-return-statements
    async def parse_api_message(
        self,
        message: str,
    ) -> ModelEvents.EventBase | list[ModelEvents.EventBase] | None:
        """Translate a Gemini Live WebSocket frame into model event(s).

        Args:
            message (`str`):
                A single decoded text frame from the Gemini WebSocket.

        Returns:
            `ModelEvents.EventBase | list[ModelEvents.EventBase] | None`:
                Parsed event(s), or ``None`` if the frame carries no
                actionable state.
        """
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict):
            return None

        # ---- Setup ----
        if "setupComplete" in data:
            return ModelEvents.ModelSessionCreatedEvent(
                session_id="gemini_session",
            )

        # ---- Server content ----
        if "serverContent" in data:
            return self._parse_server_content(data["serverContent"])

        # ---- Tool call ----
        if "toolCall" in data:
            return self._parse_tool_call(data["toolCall"])

        # ---- Session resumption update ----
        if "sessionResumptionUpdate" in data:
            update = data["sessionResumptionUpdate"]
            new_handle = update.get("newHandle") or update.get(
                "new_handle",
            )
            if new_handle:
                self._session_handle = new_handle
                return ModelEvents.ModelSessionResumptionEvent(
                    handle=new_handle,
                )
            return None

        # ---- Tool call cancellation ----
        if "toolCallCancellation" in data:
            logger.info(
                "Tool call cancelled: %s",
                data["toolCallCancellation"],
            )
            response_id = self._response_id or ""
            self._response_id = None
            return ModelEvents.ModelResponseDoneEvent(
                response_id=response_id,
                input_tokens=0,
                output_tokens=0,
            )

        # ---- Error ----
        if "error" in data:
            error = data["error"]
            return ModelEvents.ModelErrorEvent(
                error_type=error.get("status", "unknown"),
                code=str(error.get("code", "unknown")),
                message=error.get("message", "Unknown Gemini error."),
            )

        logger.debug(
            "GeminiRealtimeModel: unhandled frame keys: %s",
            list(data.keys()),
        )
        return None

    def _parse_server_content(
        self,
        server_content: dict,
    ) -> ModelEvents.EventBase | list[ModelEvents.EventBase] | None:
        """Parse a ``serverContent`` message from the Gemini API.

        Gemini 3.1+ may include multiple fields in a single serverContent
        (e.g. modelTurn + outputTranscription), so we collect all events
        before returning.
        """
        events: list[ModelEvents.EventBase] = []

        # Model turn (audio / text response)
        if "modelTurn" in server_content:
            result = self._parse_model_turn(server_content["modelTurn"])
            if result is not None:
                if isinstance(result, list):
                    events.extend(result)
                else:
                    events.append(result)

        # Output transcription (may coexist with modelTurn in 3.1+)
        if "outputTranscription" in server_content:
            text = server_content["outputTranscription"].get("text", "")
            if text:
                events.append(
                    ModelEvents.ModelResponseAudioTranscriptDeltaEvent(
                        response_id=self._response_id or "",
                        delta=text,
                        item_id="",
                    ),
                )

        # Input transcription
        if "inputTranscription" in server_content:
            text = server_content["inputTranscription"].get("text", "")
            if text:
                events.append(
                    ModelEvents.ModelInputTranscriptionDoneEvent(
                        transcript=text,
                        item_id="",
                    ),
                )

        # Generation complete
        if "generationComplete" in server_content:
            response_id = self._response_id or ""
            self._response_id = None
            events.append(
                ModelEvents.ModelResponseDoneEvent(
                    response_id=response_id,
                    input_tokens=0,
                    output_tokens=0,
                ),
            )

        # Turn complete
        if "turnComplete" in server_content:
            if self._response_id:
                response_id = self._response_id
                self._response_id = None
                events.append(
                    ModelEvents.ModelResponseDoneEvent(
                        response_id=response_id,
                        input_tokens=0,
                        output_tokens=0,
                    ),
                )

        # Interrupted
        if "interrupted" in server_content:
            pass

        if not events:
            return None
        return events[0] if len(events) == 1 else events

    def _parse_model_turn(
        self,
        model_turn: dict,
    ) -> ModelEvents.EventBase | list[ModelEvents.EventBase] | None:
        """Parse a ``modelTurn`` within ``serverContent``."""
        parts = model_turn.get("parts", [])
        if not parts:
            return None

        events: list[ModelEvents.EventBase] = []
        for part in parts:
            # Audio data
            if "inlineData" in part:
                inline_data = part["inlineData"]
                mime_type = inline_data.get("mimeType", "")
                if not mime_type.startswith("audio/"):
                    continue
                audio_data = inline_data.get("data", "")
                if not audio_data:
                    continue
                response_id = self._ensure_response_id()
                events.append(
                    ModelEvents.ModelResponseAudioDeltaEvent(
                        response_id=response_id,
                        item_id="",
                        delta=audio_data,
                        format=AudioFormat(
                            type="audio/pcm",
                            rate=self.output_sample_rate,
                        ),
                    ),
                )

            # Text data
            elif "text" in part:
                text_data = part["text"]
                if text_data:
                    response_id = self._ensure_response_id()
                    events.append(
                        ModelEvents.ModelResponseAudioTranscriptDeltaEvent(
                            response_id=response_id,
                            delta=text_data,
                            item_id="",
                        ),
                    )

        if not events:
            return None
        return events[0] if len(events) == 1 else events

    def _parse_tool_call(
        self,
        tool_call: dict,
    ) -> list[ModelEvents.EventBase] | None:
        """Parse a ``toolCall`` message from the Gemini API."""
        function_calls = tool_call.get("functionCalls", [])
        if not function_calls:
            return None

        events: list[ModelEvents.EventBase] = []
        for func_call in function_calls:
            name = func_call.get("name", "")
            call_id = func_call.get("id", "")
            args = func_call.get("args", {})

            events.append(
                ModelEvents.ModelResponseToolCallDoneEvent(
                    response_id=self._response_id or "",
                    item_id="",
                    tool_call=ToolCallBlock(
                        id=call_id,
                        name=name,
                        input=json.dumps(args, ensure_ascii=False),
                    ),
                ),
            )

        return events if events else None
