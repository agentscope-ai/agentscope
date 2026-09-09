# -*- coding: utf-8 -*-
"""The xAI Grok voice realtime model."""
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
from ...credential import XAICredential
from ...message import TextBlock, ToolCallBlock, ToolResultBlock


class XAIRealtimeModel(RealtimeModelBase):
    """The Grok voice speech-to-speech API over WebSocket.

    OpenAI-shaped, with three xAI departures: the audio format lives in a
    nested ``session.audio`` block, the reply-side frames are the GA names
    (``response.output_audio.delta``), and one response spans several
    items, so events are grouped by ``response_id``. The docs list no
    ``conversation.item.truncate``, so an interrupted reply is not
    corrected on the provider side.
    """

    class Parameters(RealtimeModelBase.Parameters):
        """Tuneables surfaced to the UI."""

        voice: str = Field(
            default="eve",
            title="Voice",
            description="A built-in voice name or a custom voice ID.",
        )
        turn_detection: Literal["server_vad", "none"] = Field(
            default="server_vad",
            title="Turn Detection",
            description="``none`` hands endpointing to the caller.",
        )
        vad_threshold: float = Field(default=0.85, ge=0.1, le=0.9)
        vad_silence_duration_ms: int = Field(default=500, ge=0, le=10000)
        reasoning_effort: Literal["high", "none"] = Field(
            default="high",
            title="Reasoning Effort",
            description="``none`` answers faster and more shallowly.",
        )
        language_hint: str | None = Field(
            default=None,
            title="Language Hint",
            description="BCP-47 code biasing transcription, e.g. ``es-MX``.",
        )

    type = "xai_realtime"
    truncation = TruncationSupport.NONE
    supports_text_input = True

    def __init__(
        self,
        model_name: str,
        credential: XAICredential,
        parameters: "XAIRealtimeModel.Parameters | None" = None,
        model_card: RealtimeModelCard | None = None,
    ) -> None:
        """Initialize the xAI realtime model.

        Args:
            model_name (`str`):
                The model name, e.g. ``"grok-voice-latest"``.
            credential (`XAICredential`):
                The xAI credential.
            parameters (`XAIRealtimeModel.Parameters | None`, optional):
                Provider parameters; defaults are used if omitted.
            model_card (`RealtimeModelCard | None`, optional):
                The model card, looked up by name if omitted.
        """
        super().__init__(model_name, credential, parameters, model_card)
        self.parameters: XAIRealtimeModel.Parameters
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._queue: asyncio.Queue[me.ModelEvent | None] = asyncio.Queue()
        self._response_id = ""
        self._tool_args: dict[str, str] = {}

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

        credential: XAICredential = self.credential  # type: ignore
        self._ws = await websockets.connect(
            f"wss://{credential.api_host}/v1/realtime"
            f"?model={self.model_name}",
            additional_headers={
                "Authorization": f"Bearer "
                f"{credential.api_key.get_secret_value()}",
            },
        )
        self._reader = asyncio.create_task(self._read(), name="xai-rt")
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
        """Append PCM16 mono audio at :attr:`input_sample_rate`."""
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            },
        )

    async def push_text(self, text: str) -> None:
        """Send a text user turn and ask for the reply."""
        await self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            },
        )
        await self.request_response()

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
        if self._response_id:
            await self.cancel_response()
        await self._send({"type": "response.create"})

    async def cancel_response(self) -> None:
        """Cancel the reply in flight, if any."""
        if self._response_id:
            await self._send({"type": "response.cancel"})

    async def truncate(
        self,
        item_id: str,
        played_ms: int,
        played_text: str,
    ) -> None:
        """No-op: the protocol documents no truncate frame."""

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
        audio_input: dict[str, Any] = {
            "format": {"type": "audio/pcm", "rate": self.input_sample_rate},
        }
        if p.language_hint:
            audio_input["transcription"] = {"language_hint": p.language_hint}
        session: dict[str, Any] = {
            "instructions": instructions,
            "voice": p.voice,
            "reasoning": {"effort": p.reasoning_effort},
            # Manual turns are ``type: null``, not a null block.
            "turn_detection": {"type": None}
            if p.turn_detection == "none"
            else {
                "type": "server_vad",
                "threshold": p.vad_threshold,
                "silence_duration_ms": p.vad_silence_duration_ms,
            },
            "audio": {
                "input": audio_input,
                "output": {
                    "format": {
                        "type": "audio/pcm",
                        "rate": self.output_sample_rate,
                    },
                },
            },
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
                    logger.exception("XAIRealtimeModel: bad frame")
                    continue
                if event is not None:
                    self._queue.put_nowait(event)
        except Exception as exc:  # noqa: BLE001
            logger.error("XAIRealtimeModel: connection lost: %s", exc)
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
                self._response_id = data.get("response", {}).get("id", "")
                return me.ResponseCreatedEvent(item_id=self._response_id)

            case "response.done":
                response = data.get("response", {})
                usage = response.get("usage") or {}
                event = me.ResponseDoneEvent(
                    item_id=response.get("id") or self._response_id,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                )
                self._response_id = ""
                return event

            case "response.output_audio.delta":
                delta = data.get("delta")
                if not delta:
                    return None
                return me.AudioDeltaEvent(
                    item_id=self._response_id,
                    pcm=base64.b64decode(delta),
                    sample_rate=self.output_sample_rate,
                )

            case "response.output_audio_transcript.delta":
                delta = data.get("delta")
                if not delta:
                    return None
                return me.TranscriptDeltaEvent(
                    item_id=self._response_id,
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

            case "response.function_call_arguments.delta":
                call_id = data.get("call_id", "")
                self._tool_args[call_id] = self._tool_args.get(
                    call_id,
                    "",
                ) + data.get("delta", "")
                return None

            case "response.function_call_arguments.done":
                # The done frame is authoritative; accumulated deltas are
                # only the fallback for a frame that omits ``arguments``.
                call_id = data.get("call_id", "")
                accumulated = self._tool_args.pop(call_id, "")
                return me.ToolCallEvent(
                    item_id=self._response_id,
                    tool_call=ToolCallBlock(
                        id=call_id,
                        name=data.get("name", ""),
                        input=data.get("arguments") or accumulated,
                    ),
                )

            case "error":
                err = data.get("error", {})
                return me.ModelErrorEvent(
                    code=err.get("code", ""),
                    message=err.get("message", ""),
                )

            case _:
                logger.debug("XAIRealtimeModel: ignoring %s", kind)
                return None
