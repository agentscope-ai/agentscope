# -*- coding: utf-8 -*-
"""The OpenAI realtime model."""
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
from ...credential import OpenAICredential
from ...message import TextBlock, ToolCallBlock, ToolResultBlock

_OPENAI_BASE_URL = "https://api.openai.com/v1"


class OpenAIRealtimeModel(RealtimeModelBase):
    """The OpenAI Realtime API over WebSocket.

    Speaks the GA protocol: a ``session`` of type ``realtime`` carrying the
    ``audio.input`` / ``audio.output`` config, and ``response.output_audio``
    frames. PCM is 24 kHz in both directions.
    """

    class Parameters(RealtimeModelBase.Parameters):
        """Tuneables surfaced to the UI."""

        voice: Literal[
            "alloy",
            "ash",
            "ballad",
            "cedar",
            "coral",
            "echo",
            "marin",
            "sage",
            "shimmer",
            "verse",
        ] = Field(default="marin", title="Voice")
        turn_detection: Literal["server_vad", "semantic_vad", "none"] = Field(
            default="server_vad",
            title="Turn Detection",
            description="``none`` hands endpointing to the caller.",
        )
        vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
        vad_prefix_padding_ms: int = Field(default=300, ge=0)
        vad_silence_duration_ms: int = Field(default=500, ge=0)
        vad_eagerness: Literal["low", "medium", "high", "auto"] = Field(
            default="auto",
            title="Semantic VAD Eagerness",
            description="How soon ``semantic_vad`` decides the user is done.",
        )
        input_audio_transcription: str = Field(
            default="gpt-4o-mini-transcribe",
            title="Input Transcription Model",
            description="The ASR model transcribing the user's speech; an "
            "empty string turns transcription off.",
        )

    type = "openai_realtime"
    truncation = TruncationSupport.EXPLICIT
    supports_text_input = True

    def __init__(
        self,
        model: str,
        credential: OpenAICredential,
        parameters: "OpenAIRealtimeModel.Parameters | None" = None,
        model_card: RealtimeModelCard | None = None,
    ) -> None:
        """Initialize the OpenAI realtime model.

        Args:
            model (`str`):
                The model name, e.g. ``"gpt-realtime-2.1"``.
            credential (`OpenAICredential`):
                The OpenAI credential.
            parameters (`OpenAIRealtimeModel.Parameters | None`, optional):
                Provider parameters; defaults are used if omitted.
            model_card (`RealtimeModelCard | None`, optional):
                The model card, looked up by name if omitted.
        """
        super().__init__(model, credential, parameters, model_card)
        self.parameters: OpenAIRealtimeModel.Parameters
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._queue: asyncio.Queue[me.ModelEvent | None] = asyncio.Queue()
        self._response_id = ""
        self._item_id = ""

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

        credential: OpenAICredential = self.credential  # type: ignore
        base_url = (credential.base_url or _OPENAI_BASE_URL).rstrip("/")
        headers = {
            "Authorization": f"Bearer "
            f"{credential.api_key.get_secret_value()}",
        }
        if credential.organization:
            headers["OpenAI-Organization"] = credential.organization

        # ``https`` -> ``wss``, ``http`` -> ``ws``.
        url = base_url.replace("http", "ws", 1)
        self._ws = await websockets.connect(
            f"{url}/realtime?model={self.model}",
            additional_headers=headers,
        )
        self._reader = asyncio.create_task(self._read(), name="openai-rt")
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
        """Append PCM16 mono audio at 24 kHz to the input buffer."""
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
        """Cut the assistant item back to the audio the user heard. The
        server drops the transcript tail itself, so *played_text* is
        unused."""
        await self._send(
            {
                "type": "conversation.item.truncate",
                "item_id": item_id,
                "content_index": 0,
                "audio_end_ms": played_ms,
            },
        )

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
        turn_detection: dict[str, Any] | None = None
        if p.turn_detection == "server_vad":
            turn_detection = {
                "type": "server_vad",
                "threshold": p.vad_threshold,
                "prefix_padding_ms": p.vad_prefix_padding_ms,
                "silence_duration_ms": p.vad_silence_duration_ms,
            }
        elif p.turn_detection == "semantic_vad":
            turn_detection = {
                "type": "semantic_vad",
                "eagerness": p.vad_eagerness,
            }

        audio_input: dict[str, Any] = {
            "format": {"type": "audio/pcm", "rate": self.input_sample_rate},
            "turn_detection": turn_detection,
        }
        if p.input_audio_transcription:
            audio_input["transcription"] = {
                "model": p.input_audio_transcription,
            }

        session: dict[str, Any] = {
            "type": "realtime",
            "instructions": instructions,
            # Audio output always carries its transcript; asking for both
            # modalities at once is rejected.
            "output_modalities": ["audio"],
            "audio": {
                "input": audio_input,
                "output": {
                    "format": {
                        "type": "audio/pcm",
                        "rate": self.output_sample_rate,
                    },
                    "voice": p.voice,
                },
            },
        }
        if tools and self.card.supports_tools:
            # The toolkit's chat-style wrapper, flattened to the realtime
            # shape: ``type`` beside ``name``/``description``/``parameters``.
            session["tools"] = [
                {"type": "function", **t["function"]}
                for t in tools
                if "function" in t
            ]
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
                    logger.exception("OpenAIRealtimeModel: bad frame")
                    continue
                if event is not None:
                    self._queue.put_nowait(event)
        except Exception as exc:  # noqa: BLE001
            logger.error("OpenAIRealtimeModel: connection lost: %s", exc)
        finally:
            self._queue.put_nowait(me.SessionEndedEvent(reason="closed"))
            self._queue.put_nowait(None)

    # pylint: disable=too-many-return-statements
    def _parse(self, data: dict) -> me.ModelEvent | None:
        """Translate one server frame into a model event.

        A response is named by its first output item, the one truncation
        and playout refer to; ``response.created`` carries no item yet.
        """
        kind = data.get("type", "")

        match kind:
            case "response.created":
                self._response_id = data.get("response", {}).get("id", "")
                self._item_id = ""
                return None

            case "response.output_item.added":
                if self._item_id:
                    return None
                self._item_id = data.get("item", {}).get("id", "")
                return me.ResponseCreatedEvent(item_id=self._item_id)

            case "response.done":
                response = data.get("response", {})
                usage = response.get("usage") or {}
                event: me.ModelEvent = me.ResponseDoneEvent(
                    item_id=self._item_id,
                    input_tokens=usage.get("input_tokens") or 0,
                    output_tokens=usage.get("output_tokens") or 0,
                )
                # ``cancelled`` and ``incomplete`` still delivered a reply;
                # only ``failed`` is an error.
                if response.get("status") == "failed":
                    err = (response.get("status_details") or {}).get(
                        "error",
                    ) or {}
                    event = me.ModelErrorEvent(
                        code=err.get("code") or "response_failed",
                        message=err.get("message", ""),
                    )
                self._response_id = ""
                self._item_id = ""
                return event

            # The second name of each pair is the pre-GA one, still emitted
            # by some OpenAI-compatible deployments.
            case "response.output_audio.delta" | "response.audio.delta":
                delta = data.get("delta")
                if not delta:
                    return None
                return me.AudioDeltaEvent(
                    item_id=self._item_id,
                    pcm=base64.b64decode(delta),
                    sample_rate=self.output_sample_rate,
                )

            case (
                "response.output_audio_transcript.delta"
                | "response.audio_transcript.delta"
            ):
                delta = data.get("delta")
                if not delta:
                    return None
                return me.TranscriptDeltaEvent(
                    item_id=self._item_id,
                    delta=delta,
                )

            case "response.function_call_arguments.done":
                return me.ToolCallEvent(
                    item_id=self._item_id,
                    tool_call=ToolCallBlock(
                        id=data.get("call_id", ""),
                        name=data.get("name", ""),
                        input=data.get("arguments") or "",
                    ),
                )

            case "conversation.item.input_audio_transcription.completed":
                return me.InputTranscriptionEvent(
                    item_id=data.get("item_id", ""),
                    text=data.get("transcript", ""),
                )

            case "input_audio_buffer.speech_started":
                return me.SpeechStartedEvent(
                    item_id=data.get("item_id", ""),
                    at_ms=data.get("audio_start_ms", 0),
                )

            case "input_audio_buffer.speech_stopped":
                return me.SpeechEndedEvent(
                    item_id=data.get("item_id", ""),
                    at_ms=data.get("audio_end_ms", 0),
                )

            case "error":
                err = data.get("error", {})
                return me.ModelErrorEvent(
                    code=err.get("code") or err.get("type", ""),
                    message=err.get("message", ""),
                )

            case _:
                logger.debug("OpenAIRealtimeModel: ignoring %s", kind)
                return None
