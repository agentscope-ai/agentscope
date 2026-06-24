# -*- coding: utf-8 -*-
"""The OpenAI realtime model class.

Implements the OpenAI Realtime WebSocket API (e.g. ``gpt-realtime-1.5``).
See: https://platform.openai.com/docs/guides/realtime
"""
import json
from typing import Any, Literal

from pydantic import Field

from .._base import RealtimeModelBase
from .._events import AudioFormat, ModelEvents
from ..._logging import logger
from ...credential import OpenAICredential
from ...message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)


_OPENAI_WS_URL = "wss://api.openai.com/v1/realtime?model={model_name}"


class OpenAIRealtimeModel(RealtimeModelBase):
    """A bidirectional realtime client for the OpenAI Realtime WebSocket API.

    Example:

        .. code-block:: python

            from asyncio import Queue
            from agentscope.credential import OpenAICredential
            from agentscope.realtime import OpenAIRealtimeModel

            model = OpenAIRealtimeModel(
                model_name="gpt-realtime-1.5",
                credential=OpenAICredential(api_key=...),
            )
            queue: Queue = Queue()
            await model.connect(queue, instructions="You are a helpful agent.")
            await model.send(audio_data_block)  # base64 PCM 24kHz mono
            event = await queue.get()           # ModelEvents.* instance
            await model.disconnect()

    .. note::
        - Tools are supported via the standard OpenAI Realtime function-call
          protocol; :attr:`support_tools` is ``True``.
        - VAD is server-side (``server_vad``) by default with
          ``create_response=True``.
        - Input/output audio is 24 kHz mono PCM.
    """

    class Parameters(RealtimeModelBase.Parameters):
        """Frontend-exposed parameters for OpenAI realtime models."""

        voice: str = Field(
            default="alloy",
            title="Voice",
            description="The TTS voice for spoken responses.",
        )

        enable_input_audio_transcription: bool = Field(
            default=True,
            title="Input Audio Transcription",
            description="Whether to transcribe the user's input audio.",
        )

        input_transcription_model: str = Field(
            default="gpt-realtime-whisper",
            title="Transcription Model",
            description="The transcription model used when transcription "
            "is enabled.",
        )

        input_transcription_language: str = Field(
            default="zh",
            title="Transcription Language",
            description="ISO-639-1 language hint for input audio "
            "transcription (e.g. 'zh', 'en'). When empty, the model "
            "auto-detects the language.",
        )

    type: Literal["openai_realtime"] = "openai_realtime"
    """The type of the realtime model."""

    support_input_modalities: list[str] = ["audio", "text", "tool_result"]
    """The OpenAI realtime API accepts audio, text, and tool results."""

    support_tools: bool = True
    """The OpenAI realtime API supports function-call tools."""

    def __init__(
        self,
        model_name: str,
        credential: OpenAICredential,
        parameters: "OpenAIRealtimeModel.Parameters | None" = None,
    ) -> None:
        """Initialize the OpenAI realtime model.

        Args:
            model_name (`str`):
                The OpenAI realtime model, e.g.
                ``"gpt-realtime-1.5"``.
            credential (`OpenAICredential`):
                The OpenAI credential used for ``Authorization``.
            parameters (`OpenAIRealtimeModel.Parameters | None`, defaults \
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
        self.input_transcription_model = (
            self.parameters.input_transcription_model
        )
        self.input_transcription_language = (
            self.parameters.input_transcription_language
        )

        # The OpenAI realtime API uses 24 kHz mono PCM for both input
        # and output.
        self.input_sample_rate = 24000
        self.output_sample_rate = 24000

        self.websocket_url = _OPENAI_WS_URL.format(model_name=model_name)
        self.websocket_headers = {
            "Authorization": (
                f"Bearer {self.credential.api_key.get_secret_value()}"
            ),
        }

        # Track current response_id so audio/transcript deltas can be
        # correlated when the API frame omits it.
        self._response_id: str = ""

        # Per-call accumulator for function-call argument deltas, keyed by
        # call_id. OpenAI streams arguments as JSON-string fragments which
        # must be concatenated before they can be parsed.
        self._tool_args_accumulator: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Session config
    # ------------------------------------------------------------------

    def _build_session_config(
        self,
        instructions: str,
        tools: list[dict] | None,
        **kwargs: Any,
    ) -> dict:
        """Build the OpenAI ``session.update`` message.

        Args:
            instructions (`str`):
                System instructions.
            tools (`list[dict] | None`):
                Standard agentscope tool JSON schemas (each wrapping a
                ``function`` block). They are flattened to the Realtime API
                shape via :meth:`_format_toolkit_schema`.
            **kwargs (`Any`):
                Extra session fields merged into the payload.

        Returns:
            `dict`: The ``session.update`` message.
        """
        kwargs.pop("session_handle", None)

        session_config: dict[str, Any] = {
            "type": "realtime",
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "turn_detection": {
                        "type": "server_vad",
                        "create_response": True,
                    },
                },
                "output": {
                    "voice": self.voice,
                },
            },
            "instructions": instructions,
            **kwargs,
        }

        if self.enable_input_audio_transcription:
            transcription_cfg: dict[str, Any] = {
                "model": self.input_transcription_model,
            }
            if self.input_transcription_language:
                transcription_cfg[
                    "language"
                ] = self.input_transcription_language
            session_config["audio"]["input"][
                "transcription"
            ] = transcription_cfg

        if tools:
            session_config["tools"] = self._format_toolkit_schema(tools)

        return {"type": "session.update", "session": session_config}

    @staticmethod
    def _format_toolkit_schema(
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Flatten agentscope tool schemas into the OpenAI Realtime shape.

        The Chat Completions API wraps tools as
        ``{"type": "function", "function": {...}}`` while the Realtime API
        expects the function fields at the top level alongside
        ``"type": "function"``.

        Args:
            schemas (`list[dict[str, Any]]`):
                Tool schemas as produced by
                :meth:`agentscope.tool.Toolkit.get_tool_schemas`.

        Returns:
            `list[dict[str, Any]]`:
                Tool schemas in OpenAI Realtime format.
        """
        return [{"type": "function", **tool["function"]} for tool in schemas]

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send(
        self,
        data: DataBlock | TextBlock | ToolResultBlock,
    ) -> None:
        """Send a content block to the OpenAI realtime model.

        Args:
            data (`DataBlock | TextBlock | ToolResultBlock`):
                The block to send. Audio is delivered as a ``DataBlock``
                with ``audio/*`` ``media_type``. Text becomes a user
                conversation item. Tool results are converted to
                ``function_call_output`` items, correlated to the original
                tool call via ``ToolResultBlock.id``.
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
            else:
                logger.warning(
                    "OpenAIRealtimeModel: unsupported DataBlock "
                    "media_type %r; skipping.",
                    media_type,
                )

        elif isinstance(data, TextBlock):
            payload = self._encode_text(data)

        elif isinstance(data, ToolResultBlock):
            payload = self._encode_tool_result(data)

        else:
            raise TypeError(
                f"OpenAIRealtimeModel.send: unsupported block type "
                f"{type(data).__name__}.",
            )

        if payload is not None:
            await self._websocket.send(payload)

        # Text items don't auto-trigger a response in server_vad mode
        # (VAD only responds to audio). Send response.create explicitly.
        if isinstance(data, TextBlock):
            await self.request_response()

    @staticmethod
    def _encode_audio(block: DataBlock) -> str:
        """Encode an audio ``DataBlock`` as ``input_audio_buffer.append``.

        Only base64-source audio is accepted; URL sources would require
        pre-downloading and base64-encoding by the caller.
        """
        if not isinstance(block.source, Base64Source):
            raise ValueError(
                "OpenAIRealtimeModel: audio DataBlock must use Base64Source.",
            )
        return json.dumps(
            {
                "type": "input_audio_buffer.append",
                "audio": block.source.data,
            },
        )

    @staticmethod
    def _encode_text(block: TextBlock) -> str:
        """Encode a ``TextBlock`` as a user ``conversation.item.create``."""
        return json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": block.text},
                    ],
                },
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _encode_tool_result(block: ToolResultBlock) -> str:
        """Encode a ``ToolResultBlock`` as a ``function_call_output`` item.

        The output is normalised to a string: a raw string is forwarded
        as-is, while a list of content blocks is joined into the text of
        any ``TextBlock`` entries (non-text blocks are dropped since the
        OpenAI realtime ``function_call_output`` field only carries a
        string).
        """
        if isinstance(block.output, str):
            output_str = block.output
        else:
            parts: list[str] = []
            for entry in block.output:
                if isinstance(entry, TextBlock):
                    parts.append(entry.text)
                else:
                    logger.debug(
                        "OpenAIRealtimeModel: dropping non-text tool result "
                        "block of type %r in function_call_output.",
                        type(entry).__name__,
                    )
            output_str = "".join(parts)

        return json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": block.id,
                    "output": output_str,
                },
            },
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------
    # Incoming frame parsing
    # ------------------------------------------------------------------

    async def request_response(self) -> None:
        """Send ``response.create`` to trigger model generation.

        OpenAI requires an explicit trigger after tool results or text items
        to initiate a response in server_vad mode.  If a response is already
        in progress, cancels it first to avoid
        ``conversation_already_has_active_response`` errors.
        """
        if self._response_id:
            logger.debug(
                "OpenAIRealtimeModel: cancelling response %s "
                "before creating new one",
                self._response_id,
            )
            await self._websocket.send(
                json.dumps({"type": "response.cancel"}),
            )
        payload = json.dumps({"type": "response.create"})
        await self._websocket.send(payload)

    # pylint: disable=too-many-return-statements
    async def parse_api_message(
        self,
        message: str,
    ) -> ModelEvents.EventBase | list[ModelEvents.EventBase] | None:
        """Translate an OpenAI Realtime WebSocket frame into model event(s).

        Note: function-call argument deltas accumulate in
        :attr:`_tool_args_accumulator`; the accumulated raw JSON string is
        carried on ``ToolCallBlock.input`` for the agent to parse and
        validate against the tool schema.

        Args:
            message (`str`):
                A single decoded text frame from the OpenAI WebSocket.

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

        event_type = data.get("type", "")

        match event_type:
            # ---- Session ----
            case "session.created":
                return ModelEvents.ModelSessionCreatedEvent(
                    session_id=data.get("session", {}).get("id", ""),
                )
            case "session.updated":
                return None  # nothing actionable

            # ---- Response lifecycle ----
            case "response.created":
                self._response_id = data.get("response", {}).get("id", "")
                return ModelEvents.ModelResponseCreatedEvent(
                    response_id=self._response_id,
                )
            case "response.done":
                response = data.get("response", {})
                response_id = response.get("id", "") or self._response_id
                usage = response.get("usage", {})
                evt = ModelEvents.ModelResponseDoneEvent(
                    response_id=response_id,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                )
                self._response_id = ""
                return evt
            case "response.cancelled":
                response = data.get("response", {})
                response_id = response.get("id", "") or self._response_id
                self._response_id = ""
                return ModelEvents.ModelResponseDoneEvent(
                    response_id=response_id,
                    input_tokens=0,
                    output_tokens=0,
                )

            # ---- Audio ----
            case "response.output_audio.delta":
                audio_data = data.get("delta", "")
                if not audio_data:
                    return None
                return ModelEvents.ModelResponseAudioDeltaEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                    delta=audio_data,
                    format=AudioFormat(
                        type="audio/pcm",
                        rate=self.output_sample_rate,
                    ),
                )
            case "response.output_audio.done":
                return ModelEvents.ModelResponseAudioDoneEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                )

            # ---- Output transcript ----
            case "response.output_audio_transcript.delta":
                transcript = data.get("delta", "")
                if not transcript:
                    return None
                return ModelEvents.ModelResponseAudioTranscriptDeltaEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                    delta=transcript,
                )
            case "response.output_audio_transcript.done":
                return ModelEvents.ModelResponseAudioTranscriptDoneEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                )

            # ---- Function-call (tools) ----
            case "response.function_call_arguments.delta":
                delta = data.get("delta", "")
                call_id = data.get("call_id", "")
                if not delta or not call_id:
                    return None
                self._tool_args_accumulator[call_id] = (
                    self._tool_args_accumulator.get(call_id, "") + delta
                )
                return ModelEvents.ModelResponseToolCallDeltaEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                    tool_call=ToolCallBlock(
                        id=call_id,
                        name=data.get("name", ""),
                        input=delta,
                    ),
                )
            case "response.function_call_arguments.done":
                call_id = data.get("call_id", "")
                arguments = self._tool_args_accumulator.pop(
                    call_id,
                    data.get("arguments", ""),
                )
                return ModelEvents.ModelResponseToolCallDoneEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                    tool_call=ToolCallBlock(
                        id=call_id,
                        name=data.get("name", ""),
                        input=arguments,
                    ),
                )

            # ---- Input transcription ----
            case "conversation.item.input_audio_transcription.delta":
                delta = data.get("delta", "")
                if not delta:
                    return None
                return ModelEvents.ModelInputTranscriptionDeltaEvent(
                    item_id=data.get("item_id", ""),
                    delta=delta,
                )
            case "conversation.item.input_audio_transcription.completed":
                transcript = data.get("transcript", "")
                if not transcript:
                    return None
                return ModelEvents.ModelInputTranscriptionDoneEvent(
                    item_id=data.get("item_id", ""),
                    transcript=transcript,
                )

            # ---- VAD ----
            case "input_audio_buffer.speech_started":
                return ModelEvents.ModelInputStartedEvent(
                    item_id=data.get("item_id", ""),
                    audio_start_ms=data.get("audio_start_ms", 0),
                )
            case "input_audio_buffer.speech_stopped":
                return ModelEvents.ModelInputDoneEvent(
                    item_id=data.get("item_id", ""),
                    audio_end_ms=data.get("audio_end_ms", 0),
                )

            # ---- Error ----
            case "error":
                err = data.get("error", {})
                return ModelEvents.ModelErrorEvent(
                    error_type=err.get("type", "unknown"),
                    code=err.get("code", "unknown"),
                    message=err.get("message", "Unknown error."),
                )

            case _:
                logger.debug(
                    "OpenAIRealtimeModel: unhandled event type %r",
                    event_type,
                )
                return None
