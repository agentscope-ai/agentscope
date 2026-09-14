# -*- coding: utf-8 -*-
"""The DashScope realtime model."""
import asyncio
import base64
import json
from typing import Any, AsyncIterator, Literal

from pydantic import Field

from .. import _events as me
from .._base import (
    ModelDisconnectedError,
    RealtimeModelBase,
    TruncationSupport,
)
from .._model_card import RealtimeModelCard
from ..._logging import logger
from ...credential import DashScopeCredential
from ...message import TextBlock, ToolCallBlock, ToolResultBlock

_REALTIME_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


class DashScopeRealtimeModel(RealtimeModelBase):
    """The Qwen-Omni realtime API over WebSocket.

    The protocol is OpenAI-shaped (``session.update``,
    ``input_audio_buffer.append``, ``response.create``) but narrower: no
    text turns, no ``conversation.item.truncate``, and history that cannot
    be seeded on connect.
    """

    class Parameters(RealtimeModelBase.Parameters):
        """Tuneables surfaced to the UI."""

        voice: str = Field(default="Cherry", title="Voice")
        turn_detection: Literal["server_vad", "semantic_vad", "none"] = Field(
            default="server_vad",
            title="Turn Detection",
            description="``none`` hands endpointing to the caller.",
        )
        vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
        vad_silence_duration_ms: int = Field(default=800, ge=0)
        input_audio_transcription: bool = Field(
            default=True,
            title="Transcribe Input",
            description="Whether to transcribe the user's speech.",
        )

    type = "dashscope_omni_realtime"
    truncation = TruncationSupport.NONE
    supports_text_input = False

    def __init__(
        self,
        model: str,
        credential: DashScopeCredential,
        parameters: "DashScopeRealtimeModel.Parameters | None" = None,
        model_card: RealtimeModelCard | None = None,
    ) -> None:
        """Initialize the DashScope realtime model.

        Args:
            model (`str`):
                The model name, e.g. ``"qwen3-omni-flash-realtime"``.
            credential (`DashScopeCredential`):
                The DashScope credential.
            parameters (`DashScopeRealtimeModel.Parameters | None`, optional):
                Provider parameters; defaults are used if omitted.
            model_card (`RealtimeModelCard | None`, optional):
                The model card, looked up by name if omitted.
        """
        super().__init__(model, credential, parameters, model_card)
        self.parameters: DashScopeRealtimeModel.Parameters
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._queue: asyncio.Queue[me.ModelEvent | None] = asyncio.Queue()
        self._item_id = ""
        self._tool_args: dict[str, str] = {}
        self._tool_names: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(
        self,
        instructions: str,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> None:
        """Open the WebSocket and send the session config."""
        import websockets

        if kwargs.get("turn_detection_disabled"):
            self.parameters = self.parameters.model_copy(
                update={"turn_detection": "none"},
            )

        credential: DashScopeCredential = self.credential  # type: ignore
        self._ws = await websockets.connect(
            f"{_REALTIME_URL}?model={self.model}",
            additional_headers={
                "Authorization": f"Bearer "
                f"{credential.api_key.get_secret_value()}",
                "X-DashScope-DataInspection": "disable",
            },
        )
        self._reader = asyncio.create_task(self._read(), name="dashscope-rt")
        await self._send(self._session_update(instructions, tools))

    async def close(self) -> None:
        """Stop reading and close the WebSocket."""
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def events(self) -> AsyncIterator[me.ModelEvent]:
        """Yield events until the session ends."""
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    async def push_audio(self, pcm: bytes) -> None:
        """Append PCM16 mono audio at 16 kHz to the input buffer."""
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            },
        )

    async def push_text(self, text: str) -> None:
        """Unsupported: the API accepts audio and images only."""
        raise NotImplementedError(
            "DashScope realtime accepts no text input.",
        )

    async def push_tool_result(self, block: ToolResultBlock) -> None:
        """Send a ``function_call_output`` item."""
        output = (
            block.output
            if isinstance(block.output, str)
            else "".join(
                _.text for _ in block.output if isinstance(_, TextBlock)
            )
        )
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": block.id,
                    "output": output,
                },
            },
        )

    async def commit_turn(self) -> None:
        """Commit the input buffer as one user turn."""
        await self._send({"type": "input_audio_buffer.commit"})

    # ------------------------------------------------------------------
    # Response control
    # ------------------------------------------------------------------

    async def request_response(self) -> None:
        """Ask for a reply, cancelling one already in flight first."""
        if self._item_id:
            await self.cancel_response()
        await self._send(
            {
                "type": "response.create",
                "response": {"modalities": ["text", "audio"]},
            },
        )

    async def cancel_response(self) -> None:
        """Cancel the reply in flight, if any."""
        if self._item_id:
            await self._send({"type": "response.cancel"})

    async def truncate(
        self,
        item_id: str,
        played_ms: int,
        played_text: str,
    ) -> None:
        """No-op: the protocol has no truncate frame."""

    # ------------------------------------------------------------------
    # Wire
    # ------------------------------------------------------------------

    def _session_update(
        self,
        instructions: str,
        tools: list[dict] | None,
    ) -> dict:
        """Build the ``session.update`` message."""
        p = self.parameters
        session: dict[str, Any] = {
            "instructions": instructions,
            "modalities": ["audio", "text"],
            "voice": p.voice,
            "input_audio_format": f"pcm{self.input_sample_rate // 1000}",
            "output_audio_format": f"pcm{self.output_sample_rate // 1000}",
            "turn_detection": None
            if p.turn_detection == "none"
            else {
                "type": p.turn_detection,
                "threshold": p.vad_threshold,
                "silence_duration_ms": p.vad_silence_duration_ms,
            },
        }
        if p.input_audio_transcription:
            session["input_audio_transcription"] = {
                "model": "gummy-realtime-v1",
            }
        if tools and self.card.supports_tools:
            session["tools"] = tools
        return {"type": "session.update", "session": session}

    async def _send(self, payload: dict) -> None:
        """Send one JSON frame.

        Raises:
            `ModelDisconnectedError`: If the provider has closed the
                session, e.g. after its idle timeout.
        """
        from websockets.exceptions import ConnectionClosed

        if self._ws is None:
            raise ModelDisconnectedError("Not connected.")
        try:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except ConnectionClosed as exc:
            self._ws = None
            raise ModelDisconnectedError(str(exc.rcvd or exc)) from exc

    async def _read(self) -> None:
        """Drain the WebSocket into the event queue until it closes."""
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    event = self._parse(json.loads(raw))
                except Exception:  # noqa: BLE001
                    logger.exception("DashScopeRealtimeModel: bad frame")
                    continue
                if event is not None:
                    self._queue.put_nowait(event)
        except Exception as exc:  # noqa: BLE001
            logger.error("DashScopeRealtimeModel: connection lost: %s", exc)
        finally:
            self._queue.put_nowait(me.SessionEndedEvent(reason="closed"))
            self._queue.put_nowait(None)

    # pylint: disable=too-many-return-statements
    def _parse(self, data: dict) -> me.ModelEvent | None:
        """Translate one server frame into a model event."""
        kind = data.get("type", "")
        item_id = data.get("item_id", "")

        match kind:
            case "response.created":
                self._item_id = data.get("response", {}).get("id", "")
                return me.ResponseCreatedEvent(item_id=self._item_id)

            case "response.done":
                response = data.get("response", {})
                usage = response.get("usage") or {}
                event = me.ResponseDoneEvent(
                    item_id=response.get("id") or self._item_id,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                )
                self._item_id = ""
                return event

            case "response.audio.delta":
                delta = data.get("delta")
                if not delta:
                    return None
                return me.AudioDeltaEvent(
                    item_id=self._item_id,
                    pcm=base64.b64decode(delta),
                    sample_rate=self.output_sample_rate,
                )

            case "response.audio_transcript.delta":
                delta = data.get("delta")
                if not delta:
                    return None
                return me.TranscriptDeltaEvent(
                    item_id=self._item_id,
                    delta=delta,
                )

            case "conversation.item.input_audio_transcription.completed":
                return me.InputTranscriptionEvent(
                    item_id=item_id,
                    text=data.get("transcript", ""),
                )

            case "input_audio_buffer.speech_started":
                return me.SpeechStartedEvent(
                    item_id=item_id,
                    at_ms=data.get("audio_start_ms", 0),
                )

            case "input_audio_buffer.speech_stopped":
                return me.SpeechEndedEvent(
                    item_id=item_id,
                    at_ms=data.get("audio_end_ms", 0),
                )

            case "response.output_item.added" | "response.output_item.done":
                item = data.get("item", {})
                if item.get("type") == "function_call" and item.get("name"):
                    self._tool_names[item["call_id"]] = item["name"]
                return None

            case "response.function_call_arguments.delta":
                call_id = data.get("call_id", "")
                self._tool_args[call_id] = self._tool_args.get(
                    call_id,
                    "",
                ) + data.get("delta", "")
                if data.get("name"):
                    self._tool_names[call_id] = data["name"]
                return None

            case "response.function_call_arguments.done":
                # The done frame is authoritative; accumulated deltas are
                # only the fallback for a frame that omits ``arguments``.
                call_id = data.get("call_id", "")
                accumulated = self._tool_args.pop(call_id, "")
                arguments = data.get("arguments") or accumulated
                name = data.get("name") or self._tool_names.pop(call_id, "")
                return me.ToolCallEvent(
                    item_id=self._item_id,
                    tool_call=ToolCallBlock(
                        id=call_id,
                        name=name,
                        input=arguments,
                    ),
                )

            case "error":
                err = data.get("error", {})
                return me.ModelErrorEvent(
                    code=err.get("code", ""),
                    message=err.get("message", ""),
                )

            case _:
                logger.debug("DashScopeRealtimeModel: ignoring %s", kind)
                return None
