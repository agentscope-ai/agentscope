# -*- coding: utf-8 -*-
"""The Gemini Live API realtime model."""
import asyncio
import base64
import json
import uuid
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
from ..._utils._common import _flatten_json_schema
from ...credential import GeminiCredential
from ...message import TextBlock, ToolCallBlock, ToolResultBlock
from ...model._gemini._model import _sanitize_schema_for_gemini

_LIVE_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService"
    ".BidiGenerateContent"
)


class GeminiRealtimeModel(RealtimeModelBase):
    """The Gemini Live API over WebSocket.

    Three protocol traits shape this adapter:

    - Nothing on the wire is addressable. An item id is minted here for
      each model turn and each user turn so every event carries one.
    - Generation is implicit: a finished user turn or a tool response
      starts the reply, so :meth:`request_response` sends nothing.
    - Interruption is the server's job — it reports
      ``serverContent.interrupted`` and drops the rest of the reply
      itself — so :meth:`truncate` and :meth:`cancel_response` are no-ops.
      With caller-owned turns the caller silences the reply locally
      instead, and the server is told not to interrupt on its own.
    """

    class Parameters(RealtimeModelBase.Parameters):
        """Tuneables surfaced to the UI."""

        voice: str = Field(default="Puck", title="Voice")
        turn_detection: Literal["automatic", "none"] = Field(
            default="automatic",
            title="Turn Detection",
            description="``none`` hands endpointing to the caller.",
        )
        start_sensitivity: Literal["HIGH", "LOW"] = Field(
            default="HIGH",
            description="How eagerly speech is detected as started.",
        )
        end_sensitivity: Literal["HIGH", "LOW"] = Field(
            default="HIGH",
            description="How eagerly speech is detected as ended.",
        )
        prefix_padding_ms: int = Field(default=300, ge=0)
        silence_duration_ms: int = Field(default=800, ge=0)
        input_audio_transcription: bool = Field(
            default=True,
            title="Transcribe Input",
            description="Whether to transcribe the user's speech.",
        )

    type = "gemini_realtime"
    truncation = TruncationSupport.SERVER
    supports_text_input = True

    def __init__(
        self,
        model: str,
        credential: GeminiCredential,
        parameters: "GeminiRealtimeModel.Parameters | None" = None,
        model_card: RealtimeModelCard | None = None,
    ) -> None:
        """Initialize the Gemini realtime model.

        Args:
            model (`str`):
                The model name, e.g. ``"gemini-3.1-flash-live-preview"``.
            credential (`GeminiCredential`):
                The Gemini credential.
            parameters (`GeminiRealtimeModel.Parameters | None`, optional):
                Provider parameters; defaults are used if omitted.
            model_card (`RealtimeModelCard | None`, optional):
                The model card, looked up by name if omitted.
        """
        super().__init__(model, credential, parameters, model_card)
        self.parameters: GeminiRealtimeModel.Parameters
        self.resumption_handle = ""
        """The latest handle the server offered. Not used on reconnect:
        the agent carries the transcript in the instructions instead."""
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._queue: asyncio.Queue[me.ModelEvent | None] = asyncio.Queue()
        self._response_id = ""
        self._user_id = ""
        self._input_text = ""
        self._usage: dict = {}
        self._activity = False
        self._ready = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(
        self,
        instructions: str,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> None:
        """Open the WebSocket and send the setup message."""
        import websockets

        if kwargs.get("turn_detection_disabled"):
            self.parameters = self.parameters.model_copy(
                update={"turn_detection": "none"},
            )
        self._response_id, self._user_id, self._input_text = "", "", ""
        self._usage, self._activity = {}, False

        credential: GeminiCredential = self.credential  # type: ignore
        self._ws = await websockets.connect(
            f"{_LIVE_URL}?key={credential.api_key.get_secret_value()}",
        )
        self._ready.clear()
        self._reader = asyncio.create_task(self._read(), name="gemini-rt")
        await self._send(
            self._setup(
                instructions,
                tools,
                kwargs.get("resumption_handle", ""),
            ),
        )
        # Realtime input is rejected until the server acknowledges setup.
        await self._ready.wait()
        if self._ws is None:
            raise ModelDisconnectedError("Session closed during setup.")

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
        """Send PCM16 mono audio at :attr:`input_sample_rate`. With
        caller-owned turns, the first chunk opens the activity window that
        :meth:`commit_turn` closes."""
        if self.parameters.turn_detection == "none" and not self._activity:
            self._activity = True
            await self._send({"realtimeInput": {"activityStart": {}}})
        await self._send(
            {
                "realtimeInput": {
                    "audio": {
                        "mimeType": f"audio/pcm;rate={self.input_sample_rate}",
                        "data": base64.b64encode(pcm).decode("ascii"),
                    },
                },
            },
        )

    async def push_text(self, text: str) -> None:
        """Send a complete text turn, which the model answers at once."""
        await self._send(
            {
                "clientContent": {
                    "turns": [{"role": "user", "parts": [{"text": text}]}],
                    "turnComplete": True,
                },
            },
        )

    async def push_tool_result(self, block: ToolResultBlock) -> None:
        """Send one ``functionResponse``; the model resumes on its own."""
        output = (
            block.output
            if isinstance(block.output, str)
            else "".join(
                _.text for _ in block.output if isinstance(_, TextBlock)
            )
        )
        await self._send(
            {
                "toolResponse": {
                    "functionResponses": [
                        {
                            "id": block.id,
                            "name": block.name,
                            "response": {"output": output},
                        },
                    ],
                },
            },
        )

    async def commit_turn(self) -> None:
        """Close the activity window, which asks for the reply."""
        if self._activity:
            self._activity = False
            await self._send({"realtimeInput": {"activityEnd": {}}})

    # ------------------------------------------------------------------
    # Response control
    # ------------------------------------------------------------------

    async def request_response(self) -> None:
        """No-op: a finished turn or a tool response is the request."""

    async def cancel_response(self) -> None:
        """No-op: the protocol has no cancel frame."""

    async def truncate(
        self,
        item_id: str,
        played_ms: int,
        played_text: str,
    ) -> None:
        """No-op: the server drops the interrupted reply itself."""

    # ------------------------------------------------------------------
    # Wire
    # ------------------------------------------------------------------

    def _setup(
        self,
        instructions: str,
        tools: list[dict] | None,
        resumption_handle: str,
    ) -> dict:
        """Build the ``setup`` message, the only one the server accepts
        first and the only place the session can be configured."""
        p = self.parameters
        if p.turn_detection == "none":
            # The caller barges in locally; letting the server do it too
            # would kill every reply, since audio keeps flowing between
            # turns and each one reopens its activity window.
            realtime_input = {
                "automaticActivityDetection": {"disabled": True},
                "activityHandling": "NO_INTERRUPTION",
            }
        else:
            realtime_input = {
                "automaticActivityDetection": {
                    "startOfSpeechSensitivity": (
                        f"START_SENSITIVITY_{p.start_sensitivity}"
                    ),
                    "endOfSpeechSensitivity": (
                        f"END_SENSITIVITY_{p.end_sensitivity}"
                    ),
                    "prefixPaddingMs": p.prefix_padding_ms,
                    "silenceDurationMs": p.silence_duration_ms,
                },
            }
        setup: dict[str, Any] = {
            "model": f"models/{self.model}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {"voiceName": p.voice},
                    },
                },
            },
            "systemInstruction": {"parts": [{"text": instructions}]},
            "realtimeInputConfig": realtime_input,
            # Audio is the only response modality, so the reply's text
            # exists nowhere else.
            "outputAudioTranscription": {},
            "sessionResumption": (
                {"handle": resumption_handle} if resumption_handle else {}
            ),
        }
        if p.input_audio_transcription:
            setup["inputAudioTranscription"] = {}
        if tools and self.card.supports_tools:
            setup["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            **schema["function"],
                            "parameters": _sanitize_schema_for_gemini(
                                _flatten_json_schema(
                                    schema["function"].get("parameters", {}),
                                ),
                            ),
                        }
                        for schema in tools
                        if "function" in schema
                    ],
                },
            ]
        return {"setup": setup}

    async def _send(self, payload: dict) -> None:
        """Send one JSON frame.

        Raises:
            `ModelDisconnectedError`: If the provider has closed the
                session, e.g. after its connection lifetime is up.
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
                    events = self._parse(json.loads(raw))
                except Exception:  # noqa: BLE001
                    logger.exception("GeminiRealtimeModel: bad frame")
                    continue
                for event in events:
                    self._queue.put_nowait(event)
        except Exception as exc:  # noqa: BLE001
            logger.error("GeminiRealtimeModel: connection lost: %s", exc)
        finally:
            self._ws = None
            self._ready.set()  # release a connect() still waiting
            self._queue.put_nowait(me.SessionEndedEvent(reason="closed"))
            self._queue.put_nowait(None)

    def _open_response(self) -> list[me.ModelEvent]:
        """Start a model turn: settle the user turn it answers, and mint
        the id every event of this turn carries."""
        if self._response_id:
            return []
        events: list[me.ModelEvent] = []
        if self._input_text:
            # Input transcription arrives in fragments; the user turn is
            # settled only once the model starts answering it.
            events.append(
                me.InputTranscriptionEvent(
                    item_id=self._user_id,
                    text=self._input_text,
                ),
            )
            self._user_id, self._input_text = "", ""
        self._response_id = uuid.uuid4().hex
        events.append(me.ResponseCreatedEvent(item_id=self._response_id))
        return events

    def _parse(self, data: dict) -> list[me.ModelEvent]:
        """Translate one server message into model events."""
        if usage := data.get("usageMetadata"):
            self._usage = usage

        if "serverContent" in data:
            return self._parse_content(data["serverContent"])

        if tool_call := data.get("toolCall"):
            # The server hands the floor over with a tool call, so the
            # reply ends here and the follow-up is a turn of its own.
            events = self._open_response()
            events += [
                me.ToolCallEvent(
                    item_id=self._response_id,
                    tool_call=ToolCallBlock(
                        id=call.get("id") or uuid.uuid4().hex,
                        name=call.get("name", ""),
                        input=json.dumps(
                            call.get("args") or {},
                            ensure_ascii=False,
                        ),
                    ),
                )
                for call in tool_call.get("functionCalls") or []
            ]
            return events + self._close_response()

        if go_away := data.get("goAway"):
            # Advance notice only; the session ends when the socket does.
            logger.info(
                "GeminiRealtimeModel: server closes in %s",
                go_away.get("timeLeft", "0s"),
            )
            return []

        if update := data.get("sessionResumptionUpdate"):
            if update.get("resumable"):
                self.resumption_handle = update.get("newHandle", "")
            return []

        if "setupComplete" in data:
            self._ready.set()
        else:
            logger.debug("GeminiRealtimeModel: ignoring %s", list(data))
        return []

    def _parse_content(self, content: dict) -> list[me.ModelEvent]:
        """Translate one ``serverContent`` message into model events."""
        events: list[me.ModelEvent] = []

        if text := (content.get("inputTranscription") or {}).get("text"):
            self._user_id = self._user_id or uuid.uuid4().hex
            self._input_text += text

        if content.get("interrupted"):
            # The only notice that the user spoke over the reply; the
            # server has already dropped the rest of it.
            self._user_id = self._user_id or uuid.uuid4().hex
            self._response_id, self._usage = "", {}
            return [me.SpeechStartedEvent(item_id=self._user_id)]

        parts = (content.get("modelTurn") or {}).get("parts") or []
        transcript = (content.get("outputTranscription") or {}).get("text", "")
        if parts or transcript:
            events += self._open_response()
        for part in parts:
            if data := (part.get("inlineData") or {}).get("data"):
                events.append(
                    me.AudioDeltaEvent(
                        item_id=self._response_id,
                        pcm=base64.b64decode(data),
                        sample_rate=self.output_sample_rate,
                    ),
                )
            elif part.get("text"):
                events.append(
                    me.TranscriptDeltaEvent(
                        item_id=self._response_id,
                        delta=part["text"],
                    ),
                )
        if transcript:
            events.append(
                me.TranscriptDeltaEvent(
                    item_id=self._response_id,
                    delta=transcript,
                ),
            )

        if content.get("turnComplete"):
            events += self._close_response()
        return events

    def _close_response(self) -> list[me.ModelEvent]:
        """End the open model turn with the usage last reported."""
        if not self._response_id:
            return []
        event = me.ResponseDoneEvent(
            item_id=self._response_id,
            input_tokens=self._usage.get("promptTokenCount", 0),
            output_tokens=self._usage.get("responseTokenCount", 0),
        )
        self._response_id, self._usage = "", {}
        return [event]
