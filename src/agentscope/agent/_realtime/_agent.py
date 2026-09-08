# -*- coding: utf-8 -*-
"""The realtime voice agent."""
import asyncio
import base64
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ...realtime import _events as me
from ._aggregator import TurnAggregator
from ...realtime._base import ModelDisconnectedError, RealtimeModelBase
from ._metrics import TurnMetrics
from ...realtime._transport._base import (
    AudioFrame,
    ControlFrame,
    ControlFrameType,
    TransportBase,
)
from ...realtime._vad import SpeechTransition, VADBase
from ..._logging import logger
from ..._utils._common import _json_loads_with_repair
from ...event import (
    AgentEvent,
    ConfirmResult,
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    ExternalExecutionResultEvent,
    ModelCallEndEvent,
    ModelCallStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    UserConfirmResultEvent,
    UserInputAudioStartEvent,
    UserInputTranscriptionEvent,
    UserInterruptEvent,
)
from ...message import (
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from ...permission import PermissionBehavior, PermissionEngine
from ...state import AgentState
from ...tool import ToolChunk, ToolResponse, Toolkit
from ...types import ReplyFinishedReason

# Audio buffered while the model is being reconnected: 10 s at 100 ms chunks.
_BACKLOG_FRAMES = 100


@dataclass
class _Reply:
    """One in-flight assistant turn, plus the text/audio alignment needed
    to work out what the user actually heard."""

    item_id: str
    text_block_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    audio_block_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    text: str = ""
    audio_ms: float = 0.0
    text_started: bool = False
    audio_started: bool = False
    marks: list[tuple[float, int]] = field(default_factory=list)

    def on_audio(self, pcm: bytes, sample_rate: int) -> None:
        """Account for one chunk of generated audio."""
        self.audio_ms += len(pcm) / (sample_rate * 2) * 1000

    def on_text(self, delta: str) -> None:
        """Record a transcript delta against the audio generated so far."""
        self.text += delta
        self.marks.append((self.audio_ms, len(self.text)))

    def spoken_prefix(self, played_ms: int) -> str:
        """The transcript prefix matching *played_ms* of playback.

        Transcript deltas usually run slightly ahead of the audio they
        describe, so this errs towards keeping one word too many.
        """
        length = 0
        for at_ms, text_len in self.marks:
            if at_ms > played_ms:
                break
            length = text_len
        return self.text[:length]


class RealtimeAgent:
    """A voice agent: a realtime model on one side, a transport on the
    other, and the turn-taking state machine in between.

    Unlike :class:`~agentscope.agent.Agent` it is bidirectional and has no
    request/reply boundary — audio flows in continuously while events flow
    out of :meth:`run`.

    Three lifetimes are kept apart on purpose. The agent owns the model
    session and the state; a transport is owned by whoever created it; a
    :meth:`run` borrows both for as long as both are alive. So a client
    can drop and reconnect without losing the model session, and the model
    session can time out during a long silence and be re-established on
    the next word without touching the transport.

    The public methods are the only entry point for discrete input. A
    transport carrying a browser's ``ControlFrame`` calls exactly those
    methods rather than reaching into the agent, so one code path handles
    the semantics whether the caller is Python or a browser. Continuous
    audio is separate and always arrives through the transport.

    Example:
        .. code-block:: python

            agent = RealtimeAgent("Friday", "Be brief.", model)
            async with agent:                              # model session
                async with LocalAudioTransport() as t:     # sound card
                    async for event in agent.run(t):       # this call
                        print(event)
    """

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model: RealtimeModelBase,
        toolkit: Toolkit | None = None,
        state: AgentState | None = None,
        vad: VADBase | None = None,
        aggregator: TurnAggregator | None = None,
    ) -> None:
        """Initialize the realtime agent.

        Args:
            name (`str`):
                Display name stamped on assistant messages and events.
            sys_prompt (`str`):
                System instructions sent to the model on connect, with the
                toolkit's skill instructions appended.
            model (`RealtimeModelBase`):
                The realtime model. Its session is opened by
                :meth:`connect` and lives as long as this agent — not as
                long as any one transport — and is re-established on the
                next user audio if the provider closes it.
            toolkit (`Toolkit | None`, optional):
                Tools the model may call. Executed here, with permission
                checks against ``state.permission_context``.
            state (`AgentState | None`, optional):
                Conversation history, permission rules and tool context.
                A new one is created if omitted.
            vad (`VADBase | None`, optional):
                Server-side voice activity detection over the audio
                arriving from the transport. ``STARTED`` cuts off a reply
                the user speaks over; ``ENDED`` closes the user's turn and
                hands it to the model. When given, the provider's own turn
                detection is switched off so that exactly one source
                decides turn boundaries; when ``None``, the provider
                decides and this agent only reacts to what it reports.
            aggregator (`TurnAggregator | None`, optional):
                Collapses the provider's transcripts into clean user turns:
                merging one that endpointing split, dropping bare
                acknowledgements. Subclass it to change what counts as a
                turn. A default with no backchannel list is used if
                omitted.
        """
        self.name = name
        self.sys_prompt = sys_prompt
        self.model = model
        self.toolkit = toolkit
        self.state = state or AgentState()
        self.vad = vad
        self.aggregator = aggregator or TurnAggregator()

        self._engine = PermissionEngine(self.state.permission_context)
        self._transport: TransportBase | None = None
        self._out: asyncio.Queue = asyncio.Queue()
        self._reply: _Reply | None = None
        self._finished_item = ""
        self._metrics = TurnMetrics()
        self._pending_tools: dict[str, ToolCallBlock] = {}
        self._confirmations: dict[str, asyncio.Future[ConfirmResult]] = {}
        self._tasks: set[asyncio.Task] = set()
        self._barge_lock = asyncio.Lock()

        # Model session: connected flag, the downlink pump that outlives
        # any transport, and reconnect bookkeeping.
        self._connected = False
        self._connected_event = asyncio.Event()
        self._downlink: asyncio.Task | None = None
        self._backlog: list[bytes] = []
        self._retry_at = 0.0
        self._backoff = 1.0

    # ------------------------------------------------------------------
    # Lifecycle: the model session
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "RealtimeAgent":
        """Connect on entry."""
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """Disconnect on exit."""
        await self.close()

    async def connect(self) -> None:
        """Open the model session. Safe to call again after the provider
        closed it; a no-op while connected."""
        if self._connected:
            return

        instructions = self.sys_prompt
        tools = None
        if self.toolkit is not None:
            groups = self.state.tool_context.activated_groups
            skills = await self.toolkit.get_skill_instructions(groups)
            if skills:
                instructions = f"{instructions}\n\n{skills}"
            if self.model.card.supports_tools:
                tools = await self.toolkit.get_tool_schemas(groups)

        # TODO(realtime): tools and instructions are sent once, here.
        # Activating a tool group or installing a skill mid-session —
        # ResetTools, the meta tool — therefore has no effect until the
        # next connect, even though the model is told it can do it.
        #
        # Fix: a `RealtimeModelBase.update_session(instructions, tools)`
        # re-sent whenever `state.tool_context.activated_groups` changes.
        # OpenAI, DashScope and xAI accept `session.update` on the open
        # connection; Gemini forbids it and must reconnect with a
        # `sessionResumption` handle plus a new `setup`, which keeps the
        # context. No provider documents whether the update applies
        # retroactively, so treat it as affecting future turns only. Do
        # not let it change `voice`: OpenAI locks it after first audio.
        await self.model.connect(
            context=self.state.context,
            instructions=instructions,
            tools=tools,
            turn_detection_disabled=self.vad is not None,
        )
        if self.vad is not None:
            self.vad.reset()
        self.aggregator.reset()
        self._connected = True
        self._connected_event.set()
        self._backoff = 1.0
        if self._downlink is None:
            self._downlink = asyncio.create_task(
                self._pump_downlink(),
                name="rt-downlink",
            )
            self._downlink.add_done_callback(self._on_downlink_done)

    async def close(self) -> None:
        """Cancel everything in flight and close the model session."""
        for future in self._confirmations.values():
            future.cancel()
        self._confirmations.clear()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._downlink is not None:
            self._downlink.cancel()
            await asyncio.gather(self._downlink, return_exceptions=True)
            self._downlink = None
        self._connected = False
        self._connected_event.clear()
        await self.model.close()

    def _on_downlink_done(self, task: asyncio.Task) -> None:
        """Surface a crashed downlink instead of a silently dead session."""
        if task.cancelled() or task.exception() is None:
            return
        self._connected = False
        self._connected_event.clear()
        logger.error(
            "RealtimeAgent: downlink pump crashed",
            exc_info=task.exception(),
        )

    async def _try_connect(self) -> bool:
        """Reconnect the model with backoff; ``False`` while still down."""
        now = time.monotonic()
        if now < self._retry_at:
            return False
        try:
            await self.connect()
        except Exception as exc:  # noqa: BLE001
            self._retry_at = now + self._backoff
            self._backoff = min(self._backoff * 2, 30.0)
            logger.warning(
                "RealtimeAgent: reconnect failed (%s); retrying in %.0fs",
                exc,
                self._backoff,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Lifecycle: one transport
    # ------------------------------------------------------------------

    async def run(self, transport: TransportBase) -> AsyncIterator[AgentEvent]:
        """Pump *transport* and yield agent events until it ends.

        The transport is borrowed, not owned: it must already be started
        and is not closed here, so the caller can keep it, reuse it or hand
        it on. The stream ends when the transport's input ends — a client
        disconnecting — not when a reply ends; there may be many replies.
        Call again with a new transport to resume the same session.

        Breaking out of the loop early does not run cleanup immediately;
        wrap the generator in :func:`contextlib.aclosing` when that
        matters.
        """
        if self._transport is not None:
            raise RuntimeError("RealtimeAgent.run is already active.")
        # Audio is forwarded as-is in both directions, so the rates must
        # already agree; resampling belongs to the transport, not here.
        pairs = {
            "input": (
                transport.input_sample_rate,
                self.model.input_sample_rate,
            ),
            "output": (
                transport.output_sample_rate,
                self.model.output_sample_rate,
            ),
        }
        if self.vad is not None:
            pairs["vad"] = (self.vad.sample_rate, transport.input_sample_rate)
        for what, (got, want) in pairs.items():
            if got != want:
                raise ValueError(
                    f"{what} sample rate mismatch: {got} Hz delivered, "
                    f"{want} Hz expected.",
                )

        self._transport = transport
        uplink = asyncio.create_task(
            self._pump_uplink(transport),
            name="rt-up",
        )
        try:
            while not uplink.done():
                getter = asyncio.ensure_future(self._out.get())
                done, _ = await asyncio.wait(
                    {getter, uplink},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if getter in done:
                    yield getter.result()
                else:
                    getter.cancel()
            # The transport is gone: cut off any reply still in flight so
            # the model stops talking to nobody, then hand the caller the
            # events that produced (the interrupted ReplyEnd) before the
            # stream ends rather than leaking them into the next run.
            await self._barge_in()
            while not self._out.empty():
                yield self._out.get_nowait()
            # A transport failure must not look like a clean disconnect.
            if not uplink.cancelled() and uplink.exception() is not None:
                raise uplink.exception()  # type: ignore[misc]
        finally:
            if not uplink.done():
                uplink.cancel()
                await asyncio.gather(uplink, return_exceptions=True)
            self._transport = None

    # ------------------------------------------------------------------
    # Discrete input
    # ------------------------------------------------------------------

    async def send(
        self,
        inputs: (
            str
            | Msg
            | UserConfirmResultEvent
            | UserInterruptEvent
            | ExternalExecutionResultEvent
        ),
    ) -> None:
        """Feed the agent anything that is not audio.

        Mirrors :meth:`Agent.reply`'s ``inputs``: a text turn (which cuts
        off any reply in progress first), the outcome of a permission
        prompt, an interrupt, or the result of a tool run elsewhere.

        Raises:
            `NotImplementedError`: For a text turn when the provider takes
                no text input, or for external execution results, which
                are not supported yet.
        """
        match inputs:
            case UserInterruptEvent():
                await self._barge_in()

            case UserConfirmResultEvent():
                for result in inputs.confirm_results:
                    future = self._confirmations.get(result.tool_call.id)
                    if future and not future.done():
                        future.set_result(result)

            case ExternalExecutionResultEvent():
                raise NotImplementedError(
                    "External tool execution is not supported yet.",
                )

            case str() | Msg():
                if not self.model.supports_text_input:
                    raise NotImplementedError(
                        f"{type(self.model).__name__} accepts no text input.",
                    )
                msg = (
                    UserMsg(name="user", content=inputs)
                    if isinstance(inputs, str)
                    else inputs
                )
                text = msg.get_text_content() or ""
                await self._barge_in()
                self.state.context.append(msg)
                await self.model.push_text(text)

    async def interrupt(self) -> None:
        """Stop the active reply, as when the user presses stop."""
        await self.send(UserInterruptEvent())

    @property
    def last_turn_metrics(self) -> TurnMetrics:
        """Latency breakdown of the most recent turn."""
        return self._metrics

    # ------------------------------------------------------------------
    # Uplink: transport -> model
    # ------------------------------------------------------------------

    async def _pump_uplink(self, transport: TransportBase) -> None:
        """Forward user audio and control frames until *transport* ends."""
        async for frame in transport.incoming():
            if isinstance(frame, AudioFrame):
                await self._on_audio(frame)
            else:
                await self._on_control(frame)

    async def _on_audio(self, frame: AudioFrame) -> None:
        """Run our VAD if we own it, reconnect if needed, forward audio."""
        speech = self.vad.push(frame.pcm) if self.vad is not None else None
        try:
            await self._forward_audio(frame.pcm, speech)
        except ModelDisconnectedError as exc:
            # The provider closed on us between two frames; the downlink
            # may not have noticed yet. Keep the frame and reconnect on
            # the next one — this is the idle-timeout path, not an error.
            self._mark_disconnected()
            self._backlog.append(frame.pcm)
            logger.info(
                "RealtimeAgent: model session closed (%s); keep talking "
                "and it reconnects on the next audio.",
                exc,
            )

    def _mark_disconnected(self) -> None:
        """Forget the model session so the next audio reconnects."""
        self._connected = False
        self._connected_event.clear()

    async def _forward_audio(
        self,
        pcm: bytes,
        speech: SpeechTransition | None,
    ) -> None:
        """Body of :meth:`_on_audio`; raises on a closed model session."""
        pushed = False
        if not self._connected:
            self._backlog.append(pcm)
            del self._backlog[:-_BACKLOG_FRAMES]
            if not await self._try_connect():
                return
            for buffered in self._backlog:
                await self.model.push_audio(buffered)
            self._backlog.clear()
            pushed = True

        if speech is SpeechTransition.STARTED:
            await self._barge_in()
        elif speech is SpeechTransition.ENDED:
            now = time.monotonic()
            self._metrics.user_speech_end_at = now
            await self.model.commit_turn()
            self._metrics.turn_committed_at = time.monotonic()

        if not pushed:
            await self.model.push_audio(pcm)

    async def _on_control(self, frame: ControlFrame) -> None:
        """Translate one upstream control frame into :meth:`send`."""
        match frame.type:
            case ControlFrameType.TEXT:
                await self.send(frame.data.get("text", ""))
            case ControlFrameType.USER_CONFIRM:
                await self.send(UserConfirmResultEvent(**frame.data))
            case ControlFrameType.INTERRUPT:
                await self.send(UserInterruptEvent())
            case ControlFrameType.EXTERNAL_EXECUTION_RESULT:
                await self.send(ExternalExecutionResultEvent(**frame.data))
            case _:
                logger.debug("RealtimeAgent: ignoring %s frame", frame.type)

    # ------------------------------------------------------------------
    # Barge-in
    # ------------------------------------------------------------------

    async def _barge_in(self) -> None:
        """Cut the reply short and correct both contexts to what was heard.

        Whether a given overlap counts as an interruption is decided
        before this is called — by the provider when it owns turn
        detection, by the VAD's own debounce otherwise.

        Reached from the uplink pump, the downlink pump, :meth:`send` and
        :meth:`run`'s exit, so it is serialised; the losers find the reply
        already closed and return.
        """
        async with self._barge_lock:
            await self._barge_in_locked()

    async def _barge_in_locked(self) -> None:
        """Body of :meth:`_barge_in`, run under the lock."""
        reply = self._reply
        if reply is None:
            return

        spoken, played_ms = (
            "",
            0,
        )  # nothing reaches the ear without a transport
        if self._transport is not None:
            position = await self._transport.clear_audio()
            if position.item_id and position.item_id != reply.item_id:
                logger.warning(
                    "RealtimeAgent: playout reports %s but %s is open; "
                    "not truncating.",
                    position.item_id,
                    reply.item_id,
                )
                return
            played_ms = position.played_ms
            spoken = reply.spoken_prefix(played_ms)

        self._truncate_reply(spoken)
        if self._connected:
            await self.model.truncate(reply.item_id, played_ms, spoken)
            await self.model.cancel_response()
        self._finish_reply(ReplyFinishedReason.INTERRUPTED)

    def _truncate_reply(self, spoken: str) -> None:
        """Rewrite the current reply in context to the part heard.

        Non-text blocks stay: a tool call that already ran belongs in the
        record even though the sentence around it was never heard.
        """
        if not self.state.context:
            return
        tail = self.state.context[-1]
        if tail.role != "assistant" or tail.name != self.name:
            return

        others = (
            []
            if isinstance(tail.content, str)
            else [_ for _ in tail.content if not isinstance(_, TextBlock)]
        )
        if spoken.strip():
            tail.content = [TextBlock(text=spoken), *others]
        elif others:
            tail.content = others
        else:
            self.state.context.pop()

    # ------------------------------------------------------------------
    # Downlink: model -> transport + events (lives with the agent)
    # ------------------------------------------------------------------

    async def _pump_downlink(self) -> None:
        """Translate model events for as long as the agent is open.

        When the provider closes the session the pump does not exit: it
        marks the model disconnected and waits for :meth:`connect` to be
        called again, which the uplink does on the next user audio.
        """
        while True:
            await self._connected_event.wait()
            async for event in self.model.events():
                await self._on_model_event(event)
            self._mark_disconnected()
            self._finish_reply(ReplyFinishedReason.ERROR)
            logger.info(
                "RealtimeAgent: model session ended; keep talking and it "
                "reconnects on the next audio.",
            )

    async def _on_model_event(self, event: me.ModelEvent) -> None:
        """Handle one model event."""
        rate = self.model.output_sample_rate
        match event:
            case me.SpeechStartedEvent():
                self._emit(
                    UserInputAudioStartEvent(
                        session_id=self.state.session_id,
                        item_id=event.item_id,
                    ),
                )
                await self._barge_in()

            case me.SpeechEndedEvent():
                # With provider turn detection this is also its commit.
                now = time.monotonic()
                self._metrics.user_speech_end_at = now
                if self.vad is None:
                    self._metrics.turn_committed_at = now

            case me.InputTranscriptionEvent():
                self._on_transcription(event)

            case me.ResponseCreatedEvent():
                self._start_reply(event.item_id)

            case me.AudioDeltaEvent():
                if self._transport is None:
                    await self._barge_in()  # nobody listening
                    return
                reply = self._start_reply(event.item_id)
                if reply is None:
                    return
                reply.on_audio(event.pcm, rate)
                await self._transport.send_audio(event.pcm, reply.item_id)
                self._emit_audio(reply, event.pcm, rate)
                self._metrics.backend_first_audio_at = (
                    self._metrics.backend_first_audio_at or time.monotonic()
                )

            case me.TranscriptDeltaEvent():
                reply = self._start_reply(event.item_id)
                if reply is None:
                    return
                reply.on_text(event.delta)
                self._emit_text(reply, event.delta)

            case me.ToolCallEvent():
                if self._start_reply(event.item_id) is not None:
                    self._pending_tools[event.tool_call.id] = event.tool_call

            case me.ResponseDoneEvent():
                self._metrics.input_tokens = event.input_tokens
                self._metrics.output_tokens = event.output_tokens
                self._finish_reply(ReplyFinishedReason.COMPLETED)
                self._schedule_tools()

            case me.ModelErrorEvent():
                logger.error(
                    "RealtimeAgent: model error %s: %s",
                    event.code,
                    event.message,
                )
                self._finish_reply(ReplyFinishedReason.ERROR)

            case me.SessionEndedEvent():
                pass  # the events() iterator ends right after this

    def _on_transcription(self, event: me.InputTranscriptionEvent) -> None:
        """Record a settled user turn, merging a split one back together."""
        turn = self.aggregator.take(event.text)
        if turn is None:
            logger.debug("RealtimeAgent: dropping %r", event.text)
            return

        if self.aggregator.merges_with_previous() and self._merge_user(turn):
            transcript = self.state.context[-1].get_text_content() or turn
        else:
            self.state.context.append(UserMsg(name="user", content=turn))
            transcript = turn

        self._emit(
            UserInputTranscriptionEvent(
                session_id=self.state.session_id,
                item_id=event.item_id,
                transcript=transcript,
            ),
        )

    def _merge_user(self, text: str) -> bool:
        """Append *text* to the previous user turn that endpointing split.

        The stub assistant message between the two halves is dropped: it
        is whatever the model managed to say before being cut off, which
        answers half a question nobody finished asking.
        """
        context = self.state.context
        if context and context[-1].role == "assistant":
            if not (context[-1].get_text_content() or "").strip():
                context.pop()
        if not context or context[-1].role != "user":
            return False
        previous = context[-1].get_text_content() or ""
        context[-1].content = [TextBlock(text=f"{previous}{text}")]
        return True

    def _start_reply(self, item_id: str) -> _Reply | None:
        """Open a reply for *item_id*, emitting its start events once.

        Returns ``None`` for an item already closed — deltas still in
        flight after a barge-in must not reopen it.
        """
        if self._reply is not None and self._reply.item_id == item_id:
            return self._reply
        if item_id == self._finished_item:
            return None

        self._reply = _Reply(item_id=item_id)
        self.state.reply_id = item_id
        self._metrics = TurnMetrics(
            user_speech_end_at=self._metrics.user_speech_end_at,
            turn_committed_at=self._metrics.turn_committed_at,
        )
        self._emit(
            ReplyStartEvent(
                session_id=self.state.session_id,
                reply_id=item_id,
                name=self.name,
            ),
        )
        self._emit(
            ModelCallStartEvent(
                reply_id=item_id,
                model_name=self.model.model_name,
            ),
        )
        return self._reply

    def _finish_reply(self, reason: ReplyFinishedReason) -> None:
        """Close the open reply, if any."""
        reply = self._reply
        if reply is None:
            return
        if self._transport is not None:
            position = self._transport.playout()
            if position.item_id == reply.item_id:
                self._metrics.first_audio_played_at = position.first_played_at
        if reply.text_started:
            self._emit(
                TextBlockEndEvent(
                    reply_id=reply.item_id,
                    block_id=reply.text_block_id,
                ),
            )
        if reply.audio_started:
            self._emit(
                DataBlockEndEvent(
                    reply_id=reply.item_id,
                    block_id=reply.audio_block_id,
                ),
            )
        self._emit(
            ModelCallEndEvent(
                reply_id=reply.item_id,
                input_tokens=self._metrics.input_tokens,
                output_tokens=self._metrics.output_tokens,
            ),
        )
        self._emit(
            ReplyEndEvent(
                session_id=self.state.session_id,
                reply_id=reply.item_id,
                finished_reason=reason,
            ),
        )
        self._finished_item = reply.item_id
        self._reply = None

    def _emit_text(self, reply: _Reply, delta: str) -> None:
        """Emit a transcript delta, opening the block on first use."""
        if not reply.text_started:
            reply.text_started = True
            self._emit(
                TextBlockStartEvent(
                    reply_id=reply.item_id,
                    block_id=reply.text_block_id,
                ),
            )
        # Grow the tail text block rather than appending one per delta,
        # or the context ends up as dozens of one-word blocks.
        tail = self.state.context[-1] if self.state.context else None
        blocks = (
            tail.content
            if tail is not None and tail.id == reply.item_id
            else None
        )
        if (
            isinstance(blocks, list)
            and blocks
            and isinstance(blocks[-1], TextBlock)
        ):
            blocks[-1].text += delta
        else:
            self.state.append_context(self.name, [TextBlock(text=delta)])
        self._emit(
            TextBlockDeltaEvent(
                reply_id=reply.item_id,
                block_id=reply.text_block_id,
                delta=delta,
            ),
        )

    def _emit_audio(self, reply: _Reply, pcm: bytes, rate: int) -> None:
        """Emit an audio delta, opening the block on first use."""
        media_type = f"audio/pcm;rate={rate}"
        if not reply.audio_started:
            reply.audio_started = True
            self._emit(
                DataBlockStartEvent(
                    reply_id=reply.item_id,
                    block_id=reply.audio_block_id,
                    media_type=media_type,
                ),
            )
        self._emit(
            DataBlockDeltaEvent(
                reply_id=reply.item_id,
                block_id=reply.audio_block_id,
                data=base64.b64encode(pcm).decode("ascii"),
                media_type=media_type,
            ),
        )

    def _emit(self, event: AgentEvent) -> None:
        """Queue one event for :meth:`run`."""
        self._out.put_nowait(event)

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def _schedule_tools(self) -> None:
        """Run the tool calls of the finished reply, then ask for more."""
        if not self._pending_tools or self.toolkit is None:
            return
        calls = list(self._pending_tools.values())
        self._pending_tools.clear()
        task = asyncio.create_task(self._run_tools(calls), name="rt-tools")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_tools(self, calls: list[ToolCallBlock]) -> None:
        """Execute *calls* in order, then trigger the follow-up response."""
        reply_id = self.state.reply_id
        try:
            for call in calls:
                await self._run_tool(reply_id, call)
            await self.model.request_response()
        except Exception:  # noqa: BLE001
            logger.exception("RealtimeAgent: tool execution failed")

    async def _run_tool(self, reply_id: str, call: ToolCallBlock) -> None:
        """Check permission for one call, run it, and report the result."""
        assert self.toolkit is not None
        self._emit(
            ToolCallStartEvent(
                reply_id=reply_id,
                tool_call_id=call.id,
                tool_call_name=call.name,
            ),
        )
        self._emit(ToolCallEndEvent(reply_id=reply_id, tool_call_id=call.id))

        try:
            tool = await self.toolkit.check_tool_available(
                call.name,
                self.state.tool_context.activated_groups,
            )
            tool_input = _json_loads_with_repair(call.input, tool.input_schema)
        except Exception as exc:  # noqa: BLE001
            await self._report_tool(reply_id, call, str(exc))
            return

        decision = await self._engine.check_permission(tool, tool_input)
        if decision.behavior in (
            PermissionBehavior.ASK,
            PermissionBehavior.PASSTHROUGH,
        ):
            confirmed = await self._ask_user(reply_id, call, decision)
            if not confirmed:
                await self._report_tool(
                    reply_id,
                    call,
                    f'Tool "{call.name}" denied by user.',
                    state=ToolResultState.DENIED,
                )
                return
        elif decision.behavior is PermissionBehavior.DENY:
            await self._report_tool(
                reply_id,
                call,
                decision.message or f'Tool "{call.name}" denied by policy.',
                state=ToolResultState.DENIED,
            )
            return

        self._emit(
            ToolResultStartEvent(
                reply_id=reply_id,
                tool_call_id=call.id,
                tool_call_name=call.name,
            ),
        )
        parts: list[str] = []
        streamed = False
        result_state = ToolResultState.SUCCESS
        try:
            async for chunk in self.toolkit.call_tool(call, self.state):
                texts = [
                    b.text for b in chunk.content if isinstance(b, TextBlock)
                ]
                if isinstance(chunk, ToolResponse):
                    # The completed result. Chunks before it were display
                    # only, so this alone is what the provider receives.
                    parts = texts
                    result_state = chunk.state
                    if not streamed:
                        for text in texts:
                            self._emit_tool_delta(reply_id, call.id, text)
                    break
                if not isinstance(chunk, ToolChunk):
                    break
                streamed = True
                for text in texts:
                    self._emit_tool_delta(reply_id, call.id, text)
        except Exception:  # noqa: BLE001
            logger.exception("RealtimeAgent: tool %s failed", call.name)
            parts = [f"Error executing tool {call.name}."]
            result_state = ToolResultState.ERROR

        await self._report_tool(
            reply_id,
            call,
            "".join(parts) or "Tool executed successfully.",
            state=result_state,
            started=True,
        )

    def _emit_tool_delta(self, reply_id: str, call_id: str, text: str) -> None:
        """Emit one fragment of tool output."""
        self._emit(
            ToolResultTextDeltaEvent(
                reply_id=reply_id,
                tool_call_id=call_id,
                delta=text,
            ),
        )

    async def _ask_user(
        self,
        reply_id: str,
        call: ToolCallBlock,
        decision: Any,
    ) -> bool:
        """Ask the user to confirm *call* and wait for the answer."""
        call.suggested_rules = decision.suggested_rules or []
        self._emit(
            RequireUserConfirmEvent(reply_id=reply_id, tool_calls=[call]),
        )
        future: asyncio.Future[
            ConfirmResult
        ] = asyncio.get_running_loop().create_future()
        self._confirmations[call.id] = future
        try:
            result = await asyncio.wait_for(future, timeout=300)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return False
        finally:
            self._confirmations.pop(call.id, None)

        for rule in result.rules or []:
            self._engine.add_rule(rule)
        return result.confirmed

    async def _report_tool(
        self,
        reply_id: str,
        call: ToolCallBlock,
        output: str,
        state: ToolResultState = ToolResultState.ERROR,
        started: bool = False,
    ) -> None:
        """Close the tool result lifecycle and send the output back."""
        if not started:
            self._emit(
                ToolResultStartEvent(
                    reply_id=reply_id,
                    tool_call_id=call.id,
                    tool_call_name=call.name,
                ),
            )
            self._emit(
                ToolResultTextDeltaEvent(
                    reply_id=reply_id,
                    tool_call_id=call.id,
                    delta=output,
                ),
            )
        self._emit(
            ToolResultEndEvent(
                reply_id=reply_id,
                tool_call_id=call.id,
                state=state,
            ),
        )
        await self.model.push_tool_result(
            ToolResultBlock(id=call.id, name=call.name, output=output),
        )
