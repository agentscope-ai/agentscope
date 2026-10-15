# -*- coding: utf-8 -*-
"""The OpenAI GPT-Live WebSocket adapter."""
import asyncio
import base64
import json
import uuid
from typing import Any, AsyncIterator

from pydantic import Field

from .. import _events as me
from .._base import (
    InteractionMode,
    ModelDisconnectedError,
    RealtimeModelBase,
)
from .._model_card import RealtimeModelCard
from ..._logging import logger
from ...credential import OpenAICredential
from ...message import ToolResultBlock

_OPENAI_BASE_URL = "https://api.openai.com/v1"
_MAX_COMMENTARY_CHARS = 400


class OpenAILiveModel(RealtimeModelBase):
    """GPT-Live over a server-side primary WebSocket.

    The adapter keeps Live audio, transcripts, and client-delegation metadata
    independent. It does not synthesize turn boundaries that the provider
    does not emit.
    """

    class Parameters(RealtimeModelBase.Parameters):
        """GPT-Live voice and connection settings."""

        voice: str = Field(
            default="marin",
            title="Voice",
            description="The voice used for generated audio.",
        )
        store: bool = Field(
            default=False,
            title="Store Session",
            description=(
                "Whether to request provider-side storage for the Live "
                "session."
            ),
        )
        startup_timeout_s: float = Field(
            default=15.0,
            gt=0,
            title="Startup Timeout",
            description=(
                "Maximum seconds to wait for the Live session to start."
            ),
        )
        close_timeout_s: float = Field(
            default=15.0,
            gt=0,
            title="Close Timeout",
            description=(
                "Maximum seconds to wait for graceful session closure."
            ),
        )

    type = "openai_live"
    """Adapter identifier stored in realtime model configurations."""

    interaction_mode = InteractionMode.FULL_DUPLEX
    """GPT-Live uses one continuous, full-duplex conversation timeline."""

    supports_text_input = False
    """Typed input is not exposed by the current GPT-Live adapter."""

    @property
    def input_transcription_enabled(self) -> bool:
        """GPT-Live always emits timestamped input transcript fragments."""
        return True

    def __init__(
        self,
        model: str,
        credential: OpenAICredential,
        parameters: "OpenAILiveModel.Parameters | None" = None,
        model_card: RealtimeModelCard | None = None,
    ) -> None:
        """Initialize the GPT-Live adapter.

        Args:
            model (`str`):
                GPT-Live model identifier, for example ``"gpt-live-1"``.
            credential (`OpenAICredential`):
                OpenAI credential used to authenticate the WebSocket.
            parameters (`OpenAILiveModel.Parameters | None`, optional):
                Voice, storage, and connection timeout settings. Defaults
                are used when omitted.
            model_card (`RealtimeModelCard | None`, optional):
                Model capabilities and audio formats. When omitted, the
                bundled card matching ``model`` is used.

        Raises:
            ValueError:
                If ``model_card`` is omitted and no bundled card matches
                ``model``.
        """
        super().__init__(model, credential, parameters, model_card)
        self.parameters: OpenAILiveModel.Parameters
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._queue: asyncio.Queue[me.ModelEvent | None] = asyncio.Queue()
        self._send_lock = asyncio.Lock()
        self._started: asyncio.Future[str] | None = None
        self._closed = asyncio.Event()
        self._session_id = ""
        self._finalized = False
        self._closing = False
        self._usage_seconds = 0.0
        self._context_usage_ratio: float | None = None

    @property
    def usage_seconds(self) -> float:
        """Return the latest cumulative voice usage snapshot."""
        return self._usage_seconds

    @property
    def context_usage_ratio(self) -> float | None:
        """Return the latest reported context-window usage ratio."""
        return self._context_usage_ratio

    async def connect(
        self,
        instructions: str,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> None:
        """Open the primary WebSocket and wait for ``session.started``.

        Args:
            instructions (`str`):
                System instructions sent in the immutable session startup
                configuration.
            tools (`list[dict] | None`, optional):
                Ignored because GPT-Live delegates tool work to the client
                session's Chat Agent.
            **kwargs (`Any`):
                Optional connection metadata. ``safety_identifier`` is sent
                as the ``OpenAI-Safety-Identifier`` header when present.

        Raises:
            asyncio.TimeoutError:
                If the provider does not acknowledge startup before
                ``startup_timeout_s``.
            ModelDisconnectedError:
                If the connection closes before startup completes.
        """
        import websockets

        await self.close()
        self._drain_queue()
        self._closed = asyncio.Event()
        self._session_id = ""
        self._finalized = False
        self._closing = False
        self._usage_seconds = 0.0
        self._context_usage_ratio = None
        self._started = asyncio.get_running_loop().create_future()

        credential: OpenAICredential = self.credential  # type: ignore
        base_url = (credential.base_url or _OPENAI_BASE_URL).rstrip("/")
        url = base_url.replace("http", "ws", 1)
        headers = {
            "Authorization": (
                f"Bearer {credential.api_key.get_secret_value()}"
            ),
        }
        if credential.organization:
            headers["OpenAI-Organization"] = credential.organization
        safety_identifier = kwargs.get("safety_identifier")
        if safety_identifier:
            headers["OpenAI-Safety-Identifier"] = safety_identifier

        try:
            self._ws = await websockets.connect(
                f"{url}/live/sessions",
                additional_headers=headers,
            )
            self._reader = asyncio.create_task(
                self._read(),
                name="openai-live",
            )
            await self._send(
                self._session_start(instructions),
            )
            await asyncio.wait_for(
                asyncio.shield(self._started),
                timeout=self.parameters.startup_timeout_s,
            )
        except Exception:
            await self._abort()
            raise

    async def close(self) -> None:
        """Gracefully finalize usage, then release the WebSocket."""
        if self._ws is None and self._reader is None:
            return
        self._closing = True
        if self._ws is not None and self._session_id and not self._finalized:
            try:
                await self._send({"type": "session.close"})
                await asyncio.wait_for(
                    self._closed.wait(),
                    timeout=self.parameters.close_timeout_s,
                )
            except (asyncio.TimeoutError, ModelDisconnectedError):
                logger.warning(
                    "OpenAILiveModel: session close was not finalized",
                )
        await self._abort()

    async def events(self) -> AsyncIterator[me.ModelEvent]:
        """Yield normalized Live events until the connection ends."""
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def push_audio(self, pcm: bytes) -> None:
        """Append complete PCM16 samples to the Live input stream.

        Args:
            pcm (`bytes`):
                Little-endian PCM16 audio at :attr:`input_sample_rate`.

        Raises:
            ValueError:
                If ``pcm`` ends with an incomplete 16-bit sample.
            ModelDisconnectedError:
                If the Live session has not started or is disconnected.
        """
        if len(pcm) % 2:
            raise ValueError("PCM16 audio must contain complete samples.")
        if not self._session_id:
            raise ModelDisconnectedError("GPT-Live session is not started.")
        await self._send(
            {
                "type": "session.input_audio.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            },
        )

    async def push_text(self, text: str) -> None:
        """Reject typed input until its delegation semantics are exposed."""
        raise NotImplementedError(
            f"{type(self).__name__} does not expose typed input yet.",
        )

    async def push_tool_result(self, block: ToolResultBlock) -> None:
        """Reject direct tools because the session chat agent owns them."""
        raise NotImplementedError(
            f"{type(self).__name__} delegates tools to the session agent.",
        )

    async def commit_turn(self) -> None:
        """Reject turn commits because GPT-Live owns a continuous stream."""
        raise NotImplementedError("GPT-Live has no user turn commit.")

    async def request_response(self) -> None:
        """Reject Responses control because this adapter uses client mode."""
        raise NotImplementedError(
            f"{type(self).__name__} uses client delegation.",
        )

    async def cancel_response(self) -> None:
        """Reject Realtime-style cancellation for the Live frontend."""
        raise NotImplementedError(
            "GPT-Live speech and backend work have separate lifecycles.",
        )

    async def interrupt_speech(self) -> None:
        """Ask the Live frontend to stop speaking without cancelling work."""
        await self._send(
            {
                "type": "session.instructions.append",
                "event_id": self._event_id("interrupt"),
                "delegation_id": None,
                "content": "Stop speaking now and listen to the user.",
            },
        )

    async def send_delegation_result(
        self,
        delegation_id: str,
        content: str,
    ) -> None:
        """Append a verified client-delegation result for speech.

        Long results are split into ordered commentary commands before they
        are returned to the Live session.

        Args:
            delegation_id (`str`):
                Identifier from the corresponding delegation-created event.
            content (`str`):
                Verified task result for GPT-Live to communicate.

        Raises:
            ValueError:
                If either argument is empty.
            ModelDisconnectedError:
                If the Live WebSocket is not connected.
        """
        if not delegation_id:
            raise ValueError("A GPT-Live delegation id is required.")
        if not content.strip():
            raise ValueError("A GPT-Live delegation result is required.")
        for chunk in self._commentary_chunks(content):
            await self._send(
                {
                    "type": "session.commentary.append",
                    "event_id": self._event_id("delegation_result"),
                    "delegation_id": delegation_id,
                    "content": chunk,
                },
            )

    @staticmethod
    def _commentary_chunks(content: str) -> list[str]:
        """Split a result conservatively below Live's 500-token limit."""
        remaining = content.strip()
        chunks: list[str] = []
        while remaining:
            if len(remaining) <= _MAX_COMMENTARY_CHARS:
                chunks.append(remaining)
                break
            boundary = remaining.rfind(" ", 0, _MAX_COMMENTARY_CHARS + 1)
            if boundary < _MAX_COMMENTARY_CHARS // 2:
                boundary = _MAX_COMMENTARY_CHARS
            chunks.append(remaining[:boundary].strip())
            remaining = remaining[boundary:].strip()
        return chunks

    def _session_start(
        self,
        instructions: str,
    ) -> dict[str, Any]:
        """Build the immutable GPT-Live startup configuration."""
        session = {
            "model": self.model,
            "instructions": instructions,
            "input": [],
            "audio": {
                "format": {
                    "type": "audio/pcm",
                    "rate": self.input_sample_rate,
                },
                "output": {"voice": self.parameters.voice},
            },
            "delegation": {"type": "client"},
            "store": self.parameters.store,
        }
        return {
            "type": "session.start",
            "event_id": self._event_id("start"),
            "session": session,
        }

    async def _send(self, payload: dict[str, Any]) -> None:
        """Send one ordered JSON command."""
        from websockets.exceptions import ConnectionClosed

        if self._ws is None:
            raise ModelDisconnectedError("GPT-Live is not connected.")
        try:
            async with self._send_lock:
                await self._ws.send(
                    json.dumps(payload, ensure_ascii=False),
                )
        except ConnectionClosed as exc:
            raise ModelDisconnectedError(str(exc.rcvd or exc)) from exc

    async def _read(self) -> None:
        """Run the connection's only receive loop."""
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    event = self._parse(json.loads(raw))
                except Exception:  # noqa: BLE001
                    logger.exception("OpenAILiveModel: invalid server frame")
                    continue
                if event is not None:
                    self._queue.put_nowait(event)
        except Exception as exc:  # noqa: BLE001
            logger.error("OpenAILiveModel: connection lost: %s", exc)
        finally:
            if not self._finalized and not self._closing:
                self._queue.put_nowait(
                    me.SessionEndedEvent(reason="connection_closed"),
                )
            if self._started is not None and not self._started.done():
                self._started.set_exception(
                    ModelDisconnectedError(
                        "Connection closed before session.started.",
                    ),
                )
            self._closed.set()
            self._queue.put_nowait(None)

    # pylint: disable=too-many-return-statements
    def _parse(self, data: dict[str, Any]) -> me.ModelEvent | None:
        """Translate one GPT-Live server event."""
        kind = data.get("type", "")

        if kind == "session.started":
            session_id = data.get("session", {}).get("id", "")
            self._session_id = session_id
            if self._started is not None and not self._started.done():
                self._started.set_result(session_id)
            return me.SessionStartedEvent(session_id=session_id)

        if kind in {
            "session.input_transcript.delta",
            "session.output_transcript.delta",
        }:
            delta = data.get("delta", "")
            if not delta:
                return None
            role = "user" if ".input_" in kind else "assistant"
            return me.TimelineTranscriptDeltaEvent(
                role=role,
                delta=delta,
                start_ms=data.get("start_ms", 0),
                end_ms=data.get("end_ms", 0),
            )

        if kind == "session.output_audio.delta":
            delta = data.get("delta", "")
            if not delta:
                return None
            return me.TimelineAudioDeltaEvent(
                pcm=base64.b64decode(delta),
                sample_rate=self.output_sample_rate,
                start_ms=data.get("start_ms"),
                end_ms=data.get("end_ms"),
            )

        if kind == "session.delegation.created":
            delegation = data.get("delegation", {})
            return me.DelegationCreatedEvent(
                delegation_id=delegation.get("id", ""),
                target=delegation.get("target", "responses"),
                response_id=delegation.get("response_id"),
                offset_ms=data.get("offset_ms", 0),
            )

        if kind == "response.event":
            return me.DelegatedResponseEvent(
                delegation_id=data.get("delegation_id", ""),
                event=data.get("event", {}),
            )

        if kind == "session.usage.updated":
            usage = data.get("usage", {})
            self._usage_seconds = float(usage.get("seconds", 0.0))
            self._context_usage_ratio = data.get("context_window", {}).get(
                "usage_ratio",
            )
            return me.SessionUsageEvent(
                seconds=self._usage_seconds,
                context_usage_ratio=self._context_usage_ratio,
            )

        if kind == "session.closed":
            usage = data.get("usage", {})
            self._usage_seconds = float(
                usage.get("seconds", self._usage_seconds),
            )
            self._finalized = True
            self._closed.set()
            return me.SessionClosedEvent(
                session_id=data.get("session", {}).get(
                    "id",
                    self._session_id,
                ),
                reason=data.get("reason", ""),
                usage_seconds=self._usage_seconds,
            )

        if kind == "error":
            error = data.get("error", {})
            message = error.get("message", "")
            if self._started is not None and not self._started.done():
                self._started.set_exception(RuntimeError(message))
            return me.ModelErrorEvent(
                code=error.get("code") or error.get("type", ""),
                message=message,
            )

        logger.debug("OpenAILiveModel: ignoring %s", kind)
        return None

    async def _abort(self) -> None:
        """Release transport resources without waiting for finalization."""
        ws = self._ws
        self._ws = None
        if ws is not None:
            await ws.close()
        reader = self._reader
        self._reader = None
        if reader is not None and reader is not asyncio.current_task():
            if not reader.done():
                reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        self._session_id = ""

    def _drain_queue(self) -> None:
        """Discard terminal events left by a previous connection."""
        while not self._queue.empty():
            self._queue.get_nowait()

    @staticmethod
    def _event_id(prefix: str) -> str:
        """Build a unique client event identifier."""
        return f"{prefix}_{uuid.uuid4().hex}"
