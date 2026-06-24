# -*- coding: utf-8 -*-
"""The DashScope realtime model class.

Implements the DashScope Realtime WebSocket API (Qwen Omni Realtime).
See: https://help.aliyun.com/zh/model-studio/qwen-omni-realtime
"""
import asyncio
import json
from typing import Any, Literal

from pydantic import Field

from .._base import RealtimeModelBase
from .._events import AudioFormat, ModelEvents
from ..._logging import logger
from ...credential import DashScopeCredential
from ...message import DataBlock, TextBlock, ToolCallBlock, ToolResultBlock


_DASHSCOPE_WS_URL = (
    "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={model_name}"
)


class DashScopeRealtimeModel(RealtimeModelBase):
    """A bidirectional realtime client for the DashScope Qwen Omni Realtime
    WebSocket API.

    Example:

        .. code-block:: python

            from asyncio import Queue
            from agentscope.credential import DashScopeCredential
            from agentscope.realtime import DashScopeRealtimeModel

            model = DashScopeRealtimeModel(
                model_name="qwen3-omni-flash-realtime",
                credential=DashScopeCredential(api_key=...),
            )
            queue: Queue = Queue()
            await model.connect(queue, instructions="You are a helpful agent.")
            await model.send(audio_data_block)  # base64 PCM 16kHz mono
            event = await queue.get()           # ModelEvents.* instance
            await model.disconnect()

    .. note::
        - Tool use is supported by the Qwen3.5-Omni-Realtime models
          via the standard DashScope realtime WebSocket protocol.
        - VAD is server-side (``server_vad``) by default.
    """

    class Parameters(RealtimeModelBase.Parameters):
        """Frontend-exposed parameters for DashScope realtime models."""

        voice: str = Field(
            default="Cherry",
            title="Voice",
            description="The TTS voice for spoken responses.",
        )

        enable_input_audio_transcription: bool = Field(
            default=True,
            title="Input Audio Transcription",
            description="Whether to transcribe the user's input audio.",
        )

        input_transcription_model: str = Field(
            default="gummy-realtime-v1",
            title="Transcription Model",
            description="The transcription model used when transcription "
            "is enabled.",
        )

        vad_silence_duration_ms: int = Field(
            default=800,
            title="VAD Silence Duration (ms)",
            description="Silence (ms) after which server VAD ends a user "
            "turn.",
            ge=0,
        )

    type: Literal["dashscope_realtime"] = "dashscope_realtime"
    """The type of the realtime model."""

    support_input_modalities: list[str] = ["audio", "image", "text"]
    """The DashScope realtime API accepts audio, image, and text input."""

    support_tools: bool = True
    """Whether the model supports function-call tools.  Determined
    dynamically from the model name at construction time; only the
    ``qwen3.5-omni-*-realtime`` family currently supports tools."""

    def __init__(
        self,
        model_name: str,
        credential: DashScopeCredential,
        parameters: "DashScopeRealtimeModel.Parameters | None" = None,
    ) -> None:
        """Initialize the DashScope realtime model.

        Args:
            model_name (`str`):
                The DashScope realtime model, e.g.
                ``"qwen3-omni-flash-realtime"``.
            credential (`DashScopeCredential`):
                The DashScope credential used for ``Authorization``.
            parameters (`DashScopeRealtimeModel.Parameters | None`, defaults \
            to `None`):
                The realtime model parameters. When ``None``, the default
                parameters will be used.
        """
        super().__init__(model_name)

        self.credential = credential
        self.parameters = parameters or self.Parameters()

        # Only qwen3.5-omni-*-realtime models support function calling
        self.support_tools = "qwen3.5-omni" in model_name.lower()

        self.voice = self.parameters.voice
        self.enable_input_audio_transcription = (
            self.parameters.enable_input_audio_transcription
        )
        self.input_transcription_model = (
            self.parameters.input_transcription_model
        )
        self.vad_silence_duration_ms = self.parameters.vad_silence_duration_ms

        self.input_sample_rate = 16000
        self.output_sample_rate = 24000

        self.websocket_url = _DASHSCOPE_WS_URL.format(model_name=model_name)
        self.websocket_headers = {
            "Authorization": (
                f"Bearer {self.credential.api_key.get_secret_value()}"
            ),
            "X-DashScope-DataInspection": "disable",
        }

        # Track current response_id so audio/transcript deltas can be
        # correlated when the API frame omits it.
        self._response_id: str = ""

        # Per-call accumulator for function-call argument deltas, keyed by
        # call_id.  DashScope streams arguments as JSON-string fragments
        # which must be concatenated before they can be parsed.
        self._tool_args_accumulator: dict[str, str] = {}
        # Map call_id → function name, populated from whichever event
        # (delta or done) first carries the name.
        self._tool_name_map: dict[str, str] = {}

        # Background task that repeatedly sends the latest image at ~1fps
        # until the next VAD commit (speech_stopped).  DashScope's image
        # buffer is designed for video-stream frames; a single append may
        # be consumed/cleared before the user speaks.
        self._image_resend_task: asyncio.Task | None = None
        self._image_resend_payload: str | None = None

    # ------------------------------------------------------------------
    # Session config
    # ------------------------------------------------------------------

    async def disconnect(self) -> None:
        """Close the session and cancel image-resend tasks."""
        self._stop_image_resend()
        await super().disconnect()

    def _build_session_config(
        self,
        instructions: str,
        tools: list[dict] | None,
        **kwargs: Any,
    ) -> dict:
        """Build the DashScope ``session.update`` message.

        Args:
            instructions (`str`):
                System instructions.
            tools (`list[dict] | None`):
                Standard agentscope tool JSON schemas (each wrapping a
                ``function`` block).  They are included as-is in the
                session config for the DashScope Realtime API.
            **kwargs (`Any`):
                Extra session fields merged into the payload.

        Returns:
            `dict`: The ``session.update`` message.
        """
        session_config: dict[str, Any] = {
            "instructions": instructions,
            "modalities": ["audio", "text"],
            "input_audio_format": f"pcm{self.input_sample_rate // 1000}",
            "output_audio_format": f"pcm{self.output_sample_rate // 1000}",
            "voice": self.voice,
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "silence_duration_ms": self.vad_silence_duration_ms,
            },
            **kwargs,
        }

        if self.enable_input_audio_transcription:
            session_config["input_audio_transcription"] = {
                "model": self.input_transcription_model,
            }

        if tools:
            session_config["tools"] = tools

        return {"type": "session.update", "session": session_config}

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send(
        self,
        data: DataBlock | TextBlock | ToolResultBlock,
    ) -> None:
        """Send a content block to the DashScope realtime model.

        Args:
            data (`DataBlock | TextBlock | ToolResultBlock`):
                The block to send.  Currently audio (``DataBlock`` with
                ``audio/*`` media_type), image (``DataBlock`` with
                ``image/*`` media_type), and text are accepted.
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
                b64_len = (
                    len(data.source.data)
                    if data.source.type == "base64"
                    else 0
                )
                logger.info(
                    "DashScopeRealtimeModel: sending image "
                    "media_type=%r base64_len=%d",
                    media_type,
                    b64_len,
                )
                await self._websocket.send(payload)
                # Start a background task that re-sends the image at ~1fps.
                # DashScope's image buffer is frame-oriented (designed for
                # video streams) — a single append may not persist until the
                # next VAD commit.  Continuous re-sending ensures the frame
                # is present in the buffer when speech_stopped fires.
                self._stop_image_resend()
                self._image_resend_payload = payload
                self._image_resend_task = asyncio.create_task(
                    self._resend_image_loop(),
                )
                payload = None
            else:
                logger.warning(
                    "DashScopeRealtimeModel: unsupported DataBlock "
                    "media_type %r; skipping.",
                    media_type,
                )

        elif isinstance(data, TextBlock):
            if self._response_id:
                logger.debug(
                    "DashScopeRealtimeModel: cancelling response"
                    " %s before creating new one",
                    self._response_id,
                )
                await self._websocket.send(
                    json.dumps({"type": "response.cancel"}),
                )
            payload = json.dumps(
                {
                    "type": "response.create",
                    "response": {"instructions": data.text},
                },
                ensure_ascii=False,
            )

        elif isinstance(data, ToolResultBlock):
            payload = self._encode_tool_result(data)
            logger.info(
                "DashScope sending tool result: %s",
                payload[:500] if payload else "(empty)",
            )

        else:
            raise TypeError(
                f"DashScopeRealtimeModel.send: unsupported block type "
                f"{type(data).__name__}.",
            )

        if payload is not None:
            await self._websocket.send(payload)

    # ------------------------------------------------------------------
    # Image resend helpers (VAD mode)
    # ------------------------------------------------------------------

    async def _resend_image_loop(self) -> None:
        """Re-send the latest image frame at ~1fps until cancelled.

        DashScope recommends sending images at 1 frame/second for video
        scenarios.  For static images in VAD mode, this ensures the frame
        persists in the server's image buffer until the next auto-commit
        (triggered by speech_stopped).
        """
        from websockets import State

        try:
            while True:
                await asyncio.sleep(1.0)
                payload = self._image_resend_payload
                if payload is None:
                    break
                if (
                    self._websocket is None
                    or self._websocket.state != State.OPEN
                ):
                    break
                try:
                    await self._websocket.send(payload)
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    def _stop_image_resend(self) -> None:
        """Cancel any active image-resend background task."""
        self._image_resend_payload = None
        if self._image_resend_task is not None:
            self._image_resend_task.cancel()
            self._image_resend_task = None

    async def request_response(self) -> None:
        """Send ``response.create`` to trigger model generation.

        DashScope requires an explicit trigger after tool results are sent
        or when non-audio content needs a response in server_vad mode.
        If a response is already in progress, cancels it first to avoid
        ``invalid_request_error`` errors.
        """
        if self._response_id:
            logger.debug(
                "DashScopeRealtimeModel: cancelling response %s "
                "before creating new one",
                self._response_id,
            )
            await self._websocket.send(
                json.dumps({"type": "response.cancel"}),
            )
        payload = json.dumps(
            {
                "type": "response.create",
                "response": {"modalities": ["text", "audio"]},
            },
        )
        await self._websocket.send(payload)

    @staticmethod
    def _encode_audio(block: DataBlock) -> str:
        """Encode an audio ``DataBlock`` as ``input_audio_buffer.append``.

        Only base64-source audio is accepted; URL sources would require
        pre-downloading and base64-encoding by the caller.
        """
        if block.source.type != "base64":
            raise ValueError(
                "DashScopeRealtimeModel: audio DataBlock must use "
                "Base64Source.",
            )
        return json.dumps(
            {
                "type": "input_audio_buffer.append",
                "audio": block.source.data,
            },
        )

    @staticmethod
    def _encode_image(block: DataBlock) -> str:
        """Encode an image ``DataBlock`` as ``input_image_buffer.append``.

        DashScope constraints:
          - Image must be JPEG format (JPG/JPEG only).
          - Base64-encoded size must not exceed 256KB.
          - Audio data must have been sent at least once before
            sending images.
          - Raw base64 (no data URL prefix).

        If the source is a URL (file:// or http(s)://), the image is
        fetched/read and base64-encoded automatically.
        """
        import base64 as b64_mod

        if block.source.type == "base64":
            data = block.source.data
        else:
            # URL source — resolve to base64
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
                "type": "input_image_buffer.append",
                "image": data,
            },
        )

    @staticmethod
    def _encode_tool_result(block: ToolResultBlock) -> str:
        """Encode a ``ToolResultBlock`` as a ``function_call_output`` item.

        The DashScope realtime protocol expects
        ``conversation.item.create`` with a ``function_call_output``
        payload.  The subsequent ``response.create`` is issued by the
        agent layer after all tool results have been sent.
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
                        "DashScopeRealtimeModel: dropping non-text tool "
                        "result block of type %r in function_call_output.",
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

    # pylint: disable=too-many-return-statements
    async def parse_api_message(
        self,
        message: str,
    ) -> ModelEvents.EventBase | list[ModelEvents.EventBase] | None:
        """Translate a DashScope WebSocket frame into model event(s).

        Args:
            message (`str`):
                A single decoded text frame from the DashScope WebSocket.

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

            # ---- Audio ----
            case "response.audio.delta":
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
            case "response.audio.done":
                return ModelEvents.ModelResponseAudioDoneEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                )

            # ---- Output transcript ----
            case "response.audio_transcript.delta":
                transcript = data.get("delta", "")
                if not transcript:
                    return None
                return ModelEvents.ModelResponseAudioTranscriptDeltaEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                    delta=transcript,
                )
            case "response.audio_transcript.done":
                return ModelEvents.ModelResponseAudioTranscriptDoneEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                )

            # ---- Input transcription ----
            case "conversation.item.input_audio_transcription.completed":
                transcript = data.get("transcript", "")
                if not transcript:
                    return None
                return ModelEvents.ModelInputTranscriptionDoneEvent(
                    item_id=data.get("item_id", ""),
                    transcript=transcript,
                )

            # ---- Function-call: item tracking ----
            case "response.output_item.added":
                item = data.get("item", {})
                if item.get("type") == "function_call":
                    call_id = item.get("call_id", "")
                    func_name = item.get("name", "")
                    if call_id and func_name:
                        self._tool_name_map[call_id] = func_name
                        logger.info(
                            "DashScope output_item.added: "
                            "call_id=%s, name=%s",
                            call_id,
                            func_name,
                        )
                return None
            case "response.output_item.done":
                item = data.get("item", {})
                if item.get("type") == "function_call":
                    call_id = item.get("call_id", "")
                    func_name = item.get("name", "")
                    if call_id and func_name:
                        self._tool_name_map[call_id] = func_name
                return None

            # ---- Function-call: argument streaming ----
            case "response.function_call_arguments.delta":
                logger.debug(
                    "DashScope raw function_call delta: %s",
                    json.dumps(data, ensure_ascii=False)[:500],
                )
                delta = data.get("delta", "")
                call_id = data.get("call_id", "")
                func_name = data.get("name", "")
                if not delta or not call_id:
                    return None
                self._tool_args_accumulator[call_id] = (
                    self._tool_args_accumulator.get(call_id, "") + delta
                )
                # Track the function name from whichever delta carries it
                if func_name:
                    self._tool_name_map[call_id] = func_name
                return ModelEvents.ModelResponseToolCallDeltaEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                    tool_call=ToolCallBlock(
                        id=call_id,
                        name=self._tool_name_map.get(call_id, func_name),
                        input=delta,
                    ),
                )
            case "response.function_call_arguments.done":
                call_id = data.get("call_id", "")
                func_name = data.get("name", "")
                arguments = self._tool_args_accumulator.pop(
                    call_id,
                    data.get("arguments", ""),
                )
                if func_name:
                    self._tool_name_map[call_id] = func_name
                resolved_name = self._tool_name_map.pop(
                    call_id,
                    func_name,
                )
                logger.info(
                    "DashScope function_call_arguments.done: "
                    "call_id=%s, name=%r, resolved=%r, args=%s",
                    call_id,
                    func_name,
                    resolved_name,
                    arguments[:200] if arguments else "(empty)",
                )
                return ModelEvents.ModelResponseToolCallDoneEvent(
                    response_id=self._response_id,
                    item_id=data.get("item_id", ""),
                    tool_call=ToolCallBlock(
                        id=call_id,
                        name=resolved_name,
                        input=arguments,
                    ),
                )

            # ---- VAD ----
            case "input_audio_buffer.speech_started":
                return ModelEvents.ModelInputStartedEvent(
                    item_id=data.get("item_id", ""),
                    audio_start_ms=data.get("audio_start_ms", 0),
                )
            case "input_audio_buffer.speech_stopped":
                # Stop re-sending the image — VAD is about to auto-commit
                # the audio + image buffers together.
                self._stop_image_resend()
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
                    "DashScopeRealtimeModel: unhandled event type %r",
                    event_type,
                )
                return None
