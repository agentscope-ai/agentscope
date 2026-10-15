# -*- coding: utf-8 -*-
"""The realtime voice agent."""
import asyncio
import base64
import time
import uuid
from collections import deque
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, TYPE_CHECKING

from ...realtime import _events as me
from ._aggregator import TurnAggregator
from ...realtime._base import (
    InteractionMode,
    ModelDisconnectedError,
    RealtimeModelBase,
)
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
    ModelCallEndEvent,
    ModelCallStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultDataDeltaEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ...message import (
    Msg,
    SystemMsg,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
    ToolResultState,
    Usage,
    UserMsg,
)
from ...permission import PermissionBehavior, PermissionEngine
from ...state import AgentState
from ...tool import ToolChunk, ToolResponse, Toolkit
from ...types import ReplyFinishedReason

if TYPE_CHECKING:
    from .._agent import Agent

# Audio buffered while the model is being reconnected: 10 s at 100 ms chunks.
_BACKLOG_FRAMES = 100
_HISTORY_MAX_MESSAGES = 100
_HISTORY_MAX_TEXT_CHARS = 32_000
_LIVE_CHECKPOINT_INTERVAL_SECS = 2.0
_DELEGATION_INPUT_TIMEOUT_SECS = 300.0


@dataclass
class _Reply:
    """One in-flight model response."""

    item_id: str
    """The provider's response item identifier."""
    reply_id: str
    """The agent's reply this response belongs to, as seen in events and
    context. A reply spans every response up to the next user turn, so
    one that calls tools has several responses."""
    text_block_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    audio_block_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    text_started: bool = False
    audio_started: bool = False


@dataclass
class _QueuedEvent:
    """One outward event and its state at a persistence boundary."""

    event: AgentEvent
    checkpoint: AgentState | None = None


@dataclass
class _ClientDelegation:
    """One application-managed task requested by a Live session."""

    delegation_id: str
    prompt: Msg
    agent: "Agent | None" = None
    prompt_id: str = ""
    baseline_ids: set[str] = field(default_factory=set)
    buffered_events: list[AgentEvent] = field(default_factory=list)
    visible: bool = False
    timeout_task: asyncio.Task[None] | None = None
    expiring: bool = False


class RealtimeAgent:
    """A voice agent: a realtime model on one side, a transport on the
    other, and the turn-taking state machine in between.

    Unlike :class:`~agentscope.agent.Agent` it is bidirectional and has no
    request/reply boundary — audio flows in continuously while events flow
    out of :meth:`reply_stream`.

    Three lifetimes are kept apart on purpose. The agent owns the model
    session and the state; a transport is owned by whoever created it; a
    :meth:`reply_stream` borrows both for as long as both are alive. So a
    client can drop and reconnect without losing the model session, and
    the model session can time out during a long silence and be
    re-established on the next word without touching the transport.

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
                    async for event in agent.reply_stream(t):       # this call
                        print(event)
    """

    def __init__(
        self,
        name: str,
        system_prompt: str,
        model: RealtimeModelBase,
        toolkit: Toolkit | None = None,
        state: AgentState | None = None,
        vad: VADBase | None = None,
        aggregator: TurnAggregator | None = None,
        delegation_agent_factory: (
            Callable[[AgentState], "Agent"] | None
        ) = None,
    ) -> None:
        """Initialize the realtime agent.

        Args:
            name (`str`):
                Display name stamped on assistant messages and events.
            system_prompt (`str`):
                System instructions sent to the model on connect, with the
                toolkit's skill instructions appended.
            model (`RealtimeModelBase`):
                The realtime model. Its session is opened by
                :meth:`connect` and lives as long as this agent — not as
                long as any one transport — and is re-established on the
                next user input if the provider closes it.
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
            delegation_agent_factory (`Callable[[AgentState], Agent] | None`,
            optional):
                Builds a chat agent for each client-delegated Live task from
                the supplied isolated session state. Its model, toolkit, and
                permission state normally come from the current persisted
                chat session.
        """
        self.name = name
        self.system_prompt = system_prompt
        self.model = model
        self.toolkit = toolkit
        self.state = state or AgentState()
        self.vad = vad
        self.aggregator = aggregator or TurnAggregator()
        self._delegation_agent_factory = delegation_agent_factory
        if (
            self.model.interaction_mode is InteractionMode.FULL_DUPLEX
            and self.vad is not None
        ):
            raise ValueError(
                "A full-duplex model cannot use client-side turn VAD.",
            )

        self._engine = PermissionEngine(self.state.permission_context)
        self._transport: TransportBase | None = None
        self._out: asyncio.Queue[_QueuedEvent] = asyncio.Queue()
        self._yielded_checkpoint: tuple[str, AgentState] | None = None
        self._reply: _Reply | None = None
        self._audio_pending = False
        self._finished_item = ""
        # The agent's open reply, and whether the next response continues
        # it (after tool results) rather than starting a new one.
        self._reply_id = ""
        self._continuing = False
        # The user's turn in flight, reported as a reply of its own.
        self._user_turn = ""
        self._user_turn_open = False
        self._metrics = TurnMetrics()
        self._pending_tools: dict[str, ToolCallBlock] = {}
        self._confirmations: dict[str, asyncio.Future[ConfirmResult]] = {}
        self._tasks: set[asyncio.Task] = set()
        self._barge_lock = asyncio.Lock()
        self._live_session_id = ""
        self._live_replies: dict[str, _Reply] = {}
        self._live_message_ids: set[str] = set()
        self._live_transcript: deque[tuple[str, str, int, int]] = deque()
        self._live_transcript_chars = 0
        self._last_live_checkpoint_at: float | None = None
        self._last_live_checkpoint_end_ms: int | None = None
        self._delegations: dict[str, _ClientDelegation] = {}
        self._delegation_queue: deque[str] = deque()
        self._active_delegation_id: str | None = None
        self._delegated_tool_calls: dict[str, str] = {}
        self._delegation_lock = asyncio.Lock()

        # Model session: connected flag, the downlink pump that outlives
        # any transport, and reconnect bookkeeping.
        self._connected = False
        self._connected_event = asyncio.Event()
        self._connection_generation = 0
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

        if self._connection_generation:
            async with self._barge_lock:
                self._end_user_turn()
                self._user_turn = ""
                self._finish_reply(ReplyFinishedReason.ERROR)
                self._finish_live_replies(ReplyFinishedReason.ERROR)
        self._connection_generation += 1

        instructions = self.system_prompt
        tools = None
        if self.toolkit is not None:
            groups = self.state.tool_context.activated_groups
            skills = await self.toolkit.get_skill_instructions(groups)
            if skills:
                instructions = f"{instructions}\n\n{skills}"
            if self.model.card.supports_tools:
                tools = await self.toolkit.get_tool_schemas(groups)

        # TODO(realtime): tools and instructions are sent once, here.
        #  Activating a tool group or installing a skill mid-session —
        #  ResetTools, the meta tool — therefore has no effect until the
        #  next connect, even though the model is told it can do it.
        #
        # Fix: a `RealtimeModelBase.update_session(instructions, tools)`
        # re-sent whenever `state.tool_context.activated_groups` changes.
        # OpenAI, DashScope and xAI accept `session.update` on the open
        # connection; Gemini forbids it and must reconnect with a
        # `sessionResumption` handle plus a new `setup`, which keeps the
        # context. No provider documents whether the update applies
        # retroactively, so treat it as affecting future turns only. Do
        # not let it change `voice`: OpenAI locks it after first audio.
        history = [
            message
            for message in self.state.context
            if message.id != self._reply_id
            and (
                message.role != "assistant"
                or message.finished_reason is not ReplyFinishedReason.ERROR
            )
        ][-_HISTORY_MAX_MESSAGES:]
        summary = self.state.summary
        if isinstance(summary, str):
            summary_text = summary
        else:
            summary_text = "\n".join(
                block.text for block in summary if isinstance(block, TextBlock)
            )
        if summary_text.strip():
            history.insert(
                0,
                SystemMsg(name="summary", content=summary_text),
            )
        if history and not self.model.supports_history_replay:
            lines = [
                f"{message.name}: {text}"
                for message in history
                if (text := message.get_text_content())
            ]
            kept: list[str] = []
            kept_chars = 0
            for line in reversed(lines):
                separator_chars = 1 if kept else 0
                if (
                    kept_chars + separator_chars + len(line)
                    > _HISTORY_MAX_TEXT_CHARS
                ):
                    break
                kept.append(line)
                kept_chars += separator_chars + len(line)
            fallback = "\n".join(reversed(kept))
            if fallback:
                instructions = (
                    f"{instructions}\n\n## Conversation so far\n{fallback}"
                )
        try:
            await self.model.connect(
                instructions=instructions,
                tools=tools,
                turn_detection_disabled=self.vad is not None,
            )
            if history and self.model.supports_history_replay:
                await self.model.replay_history(history)
        except Exception:
            await self.model.close()
            raise
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
        timeout_tasks = [
            delegation.timeout_task
            for delegation in self._delegations.values()
            if delegation.timeout_task is not None
        ]
        for task in timeout_tasks:
            task.cancel()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if timeout_tasks:
            await asyncio.gather(*timeout_tasks, return_exceptions=True)
        if self._downlink is not None:
            self._downlink.cancel()
            await asyncio.gather(self._downlink, return_exceptions=True)
            self._downlink = None
        self._connected = False
        self._connected_event.clear()
        await self.model.close()
        self._finish_live_replies(ReplyFinishedReason.COMPLETED)

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

    async def _ensure_connected(self) -> bool:
        """Re-establish the session and hand it the audio kept while it
        was down; ``False`` while the provider is still unreachable.

        The one way back to a live session, whichever input asks for it.
        """
        if self._connected:
            return True
        if not await self._try_connect():
            return False
        for buffered in self._backlog:
            await self.model.push_audio(buffered)
        self._backlog.clear()
        return True

    # ------------------------------------------------------------------
    # Lifecycle: one transport
    # ------------------------------------------------------------------

    async def reply_stream(
        self,
        transport: TransportBase,
    ) -> AsyncIterator[AgentEvent]:
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
            raise RuntimeError("RealtimeAgent.reply_stream is already active.")
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
                    queued = getter.result()
                    self._yielded_checkpoint = (
                        (queued.event.id, queued.checkpoint)
                        if queued.checkpoint is not None
                        else None
                    )
                    try:
                        yield queued.event
                    finally:
                        self._yielded_checkpoint = None
                else:
                    getter.cancel()
            # The transport is gone: cut off any reply still in flight so
            # the model stops talking to nobody, then hand the caller the
            # events that produced (the interrupted ReplyEnd) before the
            # stream ends rather than leaking them into the next run.
            if self.model.interaction_mode is InteractionMode.FULL_DUPLEX:
                await self._interrupt_live_speech()
            else:
                await self._barge_in()
            while not self._out.empty():
                queued = self._out.get_nowait()
                self._yielded_checkpoint = (
                    (queued.event.id, queued.checkpoint)
                    if queued.checkpoint is not None
                    else None
                )
                try:
                    yield queued.event
                finally:
                    self._yielded_checkpoint = None
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
            | ExternalExecutionResultEvent
            | UserInterruptEvent
        ),
    ) -> None:
        """Feed the agent anything that is not audio.

        Mirrors :meth:`Agent.reply`'s ``inputs``: a text turn (which cuts
        off any reply in progress first), the outcome of a permission
        prompt, or an interrupt.

        Raises:
            `NotImplementedError`: For a text turn when the provider takes
                no text input.
            `ModelDisconnectedError`: For a text turn when the provider
                cannot be reached, or disconnects before accepting it.
        """
        match inputs:
            case UserInterruptEvent():
                await self._barge_in()

            case UserConfirmResultEvent():
                tool_call_ids = [
                    result.tool_call.id for result in inputs.confirm_results
                ]
                delegation = self._delegation_for_tool_calls(tool_call_ids)
                if delegation is not None:
                    self._schedule_client_delegation(delegation, inputs)
                    return
                for result in inputs.confirm_results:
                    future = self._confirmations.get(result.tool_call.id)
                    if future and not future.done():
                        future.set_result(result)

            case ExternalExecutionResultEvent():
                tool_call_ids = [
                    result.id for result in inputs.execution_results
                ]
                delegation = self._delegation_for_tool_calls(tool_call_ids)
                if delegation is not None:
                    self._schedule_client_delegation(delegation, inputs)
                    return
                # pylint: disable-next=logging-fstring-interpolation
                logger.warning(
                    f"RealtimeAgent: ignored external execution results "
                    f"without an active delegation: {tool_call_ids}",
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
                # A typed turn reconnects the session the way audio does,
                # and takes the audio kept meanwhile along with it.
                if not await self._ensure_connected():
                    raise ModelDisconnectedError(
                        "Provider unreachable; the text turn was not sent.",
                    )
                try:
                    await self.model.push_text(text)
                except ModelDisconnectedError:
                    self._mark_disconnected()
                    raise
                self.state.context.append(msg)

    async def interrupt(self) -> None:
        """Stop the active reply, as when the user presses stop."""
        await self.send(UserInterruptEvent(reply_id=self._reply_id))

    @property
    def last_turn_metrics(self) -> TurnMetrics:
        """Latency breakdown of the most recent turn."""
        return self._metrics

    @property
    def allows_open_user_reply_checkpoint(self) -> bool:
        """Whether an incomplete user transcript is safe to persist."""
        return self.model.interaction_mode is InteractionMode.FULL_DUPLEX

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
        """Forget the model session so the next input reconnects."""
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
            if not await self._ensure_connected():
                return
            pushed = True

        if speech is SpeechTransition.STARTED:
            self._start_user_turn()
            await self._barge_in()
        elif speech is SpeechTransition.ENDED:
            if not self.model.input_transcription_enabled:
                self._end_user_turn()
            now = time.monotonic()
            self._metrics.user_speech_end_at = now
            await self.model.commit_turn()
            self._metrics.turn_committed_at = time.monotonic()
            # With turn detection off nothing answers a committed turn
            # by itself; providers that reply on commit make this a no-op.
            await self.model.request_response()

        if not pushed:
            await self.model.push_audio(pcm)

    async def _on_control(self, frame: ControlFrame) -> None:
        """Translate one upstream control frame into :meth:`send`."""
        match frame.type:
            case ControlFrameType.TEXT:
                await self.send(frame.data.get("text", ""))
            case ControlFrameType.USER_CONFIRM:
                await self.send(UserConfirmResultEvent(**frame.data))
            case ControlFrameType.EXTERNAL_EXECUTION_RESULT:
                await self.send(ExternalExecutionResultEvent(**frame.data))
            case ControlFrameType.INTERRUPT:
                await self.send(UserInterruptEvent(reply_id=self._reply_id))
            case _:
                logger.debug("RealtimeAgent: ignoring %s frame", frame.type)

    # ------------------------------------------------------------------
    # Barge-in
    # ------------------------------------------------------------------

    async def _barge_in(self) -> None:
        """Stop playback and any response that is still being generated.

        Whether a given overlap counts as an interruption is decided
        before this is called — by the provider when it owns turn
        detection, by the VAD's own debounce otherwise.

        Reached from the uplink pump, the downlink pump, :meth:`send` and
        the exit of :meth:`reply_stream`, so it is serialised; the losers
        find the reply already closed and return.
        """
        if self.model.interaction_mode is InteractionMode.FULL_DUPLEX:
            await self._interrupt_live_speech()
            return
        async with self._barge_lock:
            await self._barge_in_locked()

    async def _interrupt_live_speech(self) -> None:
        """Stop Live speech without cancelling delegated backend work."""
        async with self._barge_lock:
            if self._transport is not None and self._audio_pending:
                await self._transport.clear_audio()
                self._audio_pending = False
            if self._connected:
                await self.model.interrupt_speech()
            self._finish_live_role(
                "assistant",
                ReplyFinishedReason.INTERRUPTED,
            )

    async def _barge_in_locked(self) -> None:
        """Body of :meth:`_barge_in`, run under the lock."""
        active_reply = self._reply
        if self._transport is not None and self._audio_pending:
            await self._transport.clear_audio()
            self._audio_pending = False

        if active_reply is not None and self._connected:
            await self.model.cancel_response()

        if self._reply_id:
            self._finish_reply(ReplyFinishedReason.INTERRUPTED)

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
            generation = self._connection_generation
            async for event in self.model.events():
                await self._on_model_event(event)
            async with self._barge_lock:
                if generation != self._connection_generation:
                    continue
                self._mark_disconnected()
                self._end_user_turn()
                self._user_turn = ""
                self._finish_reply(ReplyFinishedReason.ERROR)
                self._finish_live_replies(ReplyFinishedReason.ERROR)
            logger.info(
                "RealtimeAgent: model session ended; keep talking and it "
                "reconnects on the next audio.",
            )

    async def _on_model_event(self, event: me.ModelEvent) -> None:
        """Handle one model event."""
        if self.model.interaction_mode is InteractionMode.FULL_DUPLEX:
            await self._on_live_event(event)
            return
        rate = self.model.output_sample_rate
        match event:
            case me.SpeechStartedEvent():
                self._start_user_turn(event.item_id)
                await self._barge_in()

            case me.SpeechEndedEvent():
                if not self.model.input_transcription_enabled:
                    self._end_user_turn()
                # With provider turn detection this is also its commit.
                now = time.monotonic()
                self._metrics.user_speech_end_at = now
                if self.vad is None:
                    self._metrics.turn_committed_at = now

            case me.InputTranscriptionEvent():
                self._on_transcription(event)

            case me.InputTranscriptionFailedEvent():
                self._end_user_turn()
                self._user_turn = ""

            case me.ResponseCreatedEvent():
                self._start_reply(event.item_id)

            case me.AudioDeltaEvent():
                if self._transport is None:
                    await self._barge_in()  # nobody listening
                    return
                reply = self._start_reply(event.item_id)
                if reply is None:
                    return
                await self._transport.send_audio(event.pcm, reply.item_id)
                self._audio_pending = True
                self._emit_audio(reply, event.pcm, rate)
                self._metrics.backend_first_audio_at = (
                    self._metrics.backend_first_audio_at or time.monotonic()
                )

            case me.TranscriptDeltaEvent():
                reply = self._start_reply(event.item_id)
                if reply is None:
                    return
                self._emit_text(reply, event.delta)

            case me.ToolCallEvent():
                if self._start_reply(event.item_id) is not None:
                    self._pending_tools[event.tool_call.id] = event.tool_call
                    self.state.append_context(self.name, [event.tool_call])

            case me.ResponseDoneEvent():
                async with self._barge_lock:
                    reply = self._reply
                    if reply is None or reply.item_id != event.item_id:
                        return
                    self._metrics.input_tokens = event.input_tokens
                    self._metrics.output_tokens = event.output_tokens
                    tail = (
                        self.state.context[-1] if self.state.context else None
                    )
                    if tail is not None and tail.id == self._reply_id:
                        tail.append_usage(
                            Usage(
                                input_tokens=event.input_tokens,
                                output_tokens=event.output_tokens,
                            ),
                        )
                    if self._pending_tools and self.toolkit is not None:
                        # The reply goes on: tools run, then the next
                        # response answers with their results.
                        reply_id = reply.reply_id
                        self._finish_response()
                        self._continuing = True
                        self._schedule_tools(reply_id)
                    else:
                        self._finish_reply(ReplyFinishedReason.COMPLETED)

            case me.ModelErrorEvent():
                logger.error(
                    "RealtimeAgent: model error %s: %s",
                    event.code,
                    event.message,
                )
                async with self._barge_lock:
                    self._end_user_turn()
                    self._user_turn = ""
                    self._finish_reply(ReplyFinishedReason.ERROR)

            case me.SessionEndedEvent():
                # The events() iterator ends right after this.
                logger.info(
                    "RealtimeAgent: model closed the session (%s)",
                    event.reason,
                )

    async def _on_live_event(self, event: me.ModelEvent) -> None:
        """Project one full-duplex timeline event into public events."""
        match event:
            case me.SessionStartedEvent():
                self._live_session_id = event.session_id
                self._last_live_checkpoint_at = None
                self._last_live_checkpoint_end_ms = None

            case me.TimelineAudioDeltaEvent():
                if self._transport is None:
                    return
                reply = self._start_live_reply("assistant")
                playout_id = f"live:{self._live_session_id}"
                await self._transport.send_audio(event.pcm, playout_id)
                self._audio_pending = True
                self._emit_audio(reply, event.pcm, event.sample_rate)

            case me.TimelineTranscriptDeltaEvent():
                self._append_live_transcript(event)
                reply = self._start_live_reply(event.role)
                self._emit_live_text(
                    event.role,
                    reply,
                    event.delta,
                    event.end_ms,
                )

            case me.SessionClosedEvent():
                self._finish_live_replies(ReplyFinishedReason.COMPLETED)

            case me.ModelErrorEvent():
                logger.error(
                    "RealtimeAgent: Live model error %s: %s",
                    event.code,
                    event.message,
                )
                self._finish_live_replies(ReplyFinishedReason.ERROR)

            case me.SessionEndedEvent():
                self._finish_live_replies(ReplyFinishedReason.ERROR)

            case me.DelegationCreatedEvent(target="client"):
                self._start_client_delegation(event.delegation_id)

            case me.DelegationCreatedEvent() | me.DelegatedResponseEvent():
                logger.debug(
                    "RealtimeAgent: ignoring an unsupported delegated event",
                )

            case me.SessionUsageEvent():
                pass

    def _start_live_reply(self, role: str) -> _Reply:
        """Open one independent user or assistant Live transcript."""
        current = self._live_replies.get(role)
        if current is not None:
            return current
        reply_id = f"live:{role}:{uuid.uuid4().hex}"
        reply = _Reply(item_id=reply_id, reply_id=reply_id)
        self._live_replies[role] = reply
        self._live_message_ids.add(reply_id)
        self._emit(
            ReplyStartEvent(
                session_id=self.state.session_id,
                reply_id=reply_id,
                name="user" if role == "user" else self.name,
                role="user" if role == "user" else "assistant",
            ),
        )
        if role == "assistant":
            self._emit(
                ModelCallStartEvent(
                    reply_id=reply_id,
                    model_name=self.model.model,
                ),
            )
        return reply

    def _emit_live_text(
        self,
        role: str,
        reply: _Reply,
        delta: str,
        end_ms: int,
    ) -> None:
        """Append a timestamped Live transcript to its speaker's message."""
        if not reply.text_started:
            reply.text_started = True
            self._emit(
                TextBlockStartEvent(
                    reply_id=reply.reply_id,
                    block_id=reply.text_block_id,
                ),
            )
        message = next(
            (
                item
                for item in reversed(self.state.context)
                if item.id == reply.reply_id
            ),
            None,
        )
        if message is None:
            message = Msg(
                id=reply.reply_id,
                role="user" if role == "user" else "assistant",
                name="user" if role == "user" else self.name,
                content=[TextBlock(id=reply.text_block_id, text=delta)],
            )
            self.state.context.append(message)
        else:
            blocks = message.get_content_blocks("text")
            if blocks:
                blocks[-1].text += delta
            else:
                message.content.append(
                    TextBlock(id=reply.text_block_id, text=delta),
                )
        self._emit(
            TextBlockDeltaEvent(
                reply_id=reply.reply_id,
                block_id=reply.text_block_id,
                delta=delta,
            ),
            checkpoint=self._should_checkpoint_live_transcript(end_ms),
        )

    def _should_checkpoint_live_transcript(self, end_ms: int) -> bool:
        """Sample state when the wall or Live timeline interval advances."""
        now = time.monotonic()
        interval_ms = int(_LIVE_CHECKPOINT_INTERVAL_SECS * 1_000)
        wall_due = (
            self._last_live_checkpoint_at is None
            or now - self._last_live_checkpoint_at
            >= _LIVE_CHECKPOINT_INTERVAL_SECS
        )
        timeline_due = (
            self._last_live_checkpoint_end_ms is not None
            and end_ms - self._last_live_checkpoint_end_ms >= interval_ms
        )
        if not wall_due and not timeline_due:
            return False
        self._last_live_checkpoint_at = now
        if self._last_live_checkpoint_end_ms is None:
            self._last_live_checkpoint_end_ms = end_ms
        else:
            self._last_live_checkpoint_end_ms = max(
                self._last_live_checkpoint_end_ms,
                end_ms,
            )
        return True

    def _finish_live_role(
        self,
        role: str,
        reason: ReplyFinishedReason,
    ) -> None:
        """Close one speaker's current Live presentation segment."""
        reply = self._live_replies.pop(role, None)
        if reply is None:
            return
        if reply.text_started:
            self._emit(
                TextBlockEndEvent(
                    reply_id=reply.reply_id,
                    block_id=reply.text_block_id,
                ),
            )
        if reply.audio_started:
            self._emit(
                DataBlockEndEvent(
                    reply_id=reply.reply_id,
                    block_id=reply.audio_block_id,
                ),
            )
        if role == "assistant":
            self._emit(
                ModelCallEndEvent(
                    reply_id=reply.reply_id,
                    input_tokens=0,
                    output_tokens=0,
                ),
            )
        finished = ReplyEndEvent(
            session_id=self.state.session_id,
            reply_id=reply.reply_id,
            finished_reason=reason,
        )
        for message in reversed(self.state.context):
            if message.id == reply.reply_id:
                message.append_event(finished)
                break
        self._emit(finished)

    def _finish_live_replies(self, reason: ReplyFinishedReason) -> None:
        """Close both independently growing Live transcript streams."""
        self._finish_live_role("user", reason)
        self._finish_live_role("assistant", reason)

    def _start_client_delegation(self, delegation_id: str) -> None:
        """Queue one GPT-Live task behind the active delegation."""
        if not delegation_id or delegation_id in self._delegations:
            return
        if self._delegation_agent_factory is None:
            self._schedule_delegation_failure(delegation_id)
            return
        prompt = UserMsg(
            name="user",
            content=self._build_delegation_prompt(),
        )
        delegation = _ClientDelegation(
            delegation_id=delegation_id,
            prompt=prompt,
        )
        self._delegations[delegation_id] = delegation
        if self._active_delegation_id is None:
            self._activate_client_delegation(delegation)
        else:
            self._delegation_queue.append(delegation_id)

    def _activate_client_delegation(
        self,
        delegation: _ClientDelegation,
    ) -> None:
        """Create a backend from the latest state and start its task."""
        if self._delegation_agent_factory is None:
            self._delegations.pop(delegation.delegation_id, None)
            self._schedule_delegation_failure(delegation.delegation_id)
            self._activate_next_client_delegation()
            return
        try:
            agent = self._delegation_agent_factory(
                self._copy_delegation_state(),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "RealtimeAgent: failed to create delegation backend",
            )
            self._delegations.pop(delegation.delegation_id, None)
            self._schedule_delegation_failure(delegation.delegation_id)
            self._activate_next_client_delegation()
            return
        delegation.agent = agent
        delegation.prompt_id = delegation.prompt.id
        delegation.baseline_ids = {
            message.id for message in agent.state.context
        }
        self._active_delegation_id = delegation.delegation_id
        self._schedule_client_delegation(delegation, delegation.prompt)

    def _activate_next_client_delegation(self) -> None:
        """Start the oldest queued delegation, if one remains."""
        if self._active_delegation_id is not None:
            return
        while self._delegation_queue:
            delegation_id = self._delegation_queue.popleft()
            delegation = self._delegations.get(delegation_id)
            if delegation is not None:
                self._activate_client_delegation(delegation)
                return

    def _schedule_client_delegation(
        self,
        delegation: _ClientDelegation,
        inputs: (
            Msg
            | UserConfirmResultEvent
            | ExternalExecutionResultEvent
            | UserInterruptEvent
        ),
    ) -> None:
        """Run or resume a delegated chat-agent reply in the background."""
        self._cancel_delegation_timeout(delegation)
        task = asyncio.create_task(
            self._drive_client_delegation(delegation, inputs),
            name=f"live-delegation-{delegation.delegation_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _schedule_delegation_timeout(
        self,
        delegation: _ClientDelegation,
    ) -> None:
        """Cancel a delegation that remains parked without input."""
        self._cancel_delegation_timeout(delegation)
        delegation.timeout_task = asyncio.create_task(
            self._expire_client_delegation(delegation),
            name=f"live-delegation-timeout-{delegation.delegation_id}",
        )

    def _cancel_delegation_timeout(
        self,
        delegation: _ClientDelegation,
    ) -> None:
        """Cancel the pending external-input deadline, if any."""
        task = delegation.timeout_task
        delegation.timeout_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _expire_client_delegation(
        self,
        delegation: _ClientDelegation,
    ) -> None:
        """Interrupt and finish a delegation after its input deadline."""
        try:
            await asyncio.sleep(_DELEGATION_INPUT_TIMEOUT_SECS)
            if (
                self._delegations.get(delegation.delegation_id)
                is not delegation
                or self._active_delegation_id != delegation.delegation_id
                or delegation.agent is None
            ):
                return
            delegation.expiring = True
            await self._drive_client_delegation(
                delegation,
                UserInterruptEvent(reply_id=delegation.agent.state.reply_id),
                result_override=(
                    "The delegated task was cancelled because the required "
                    "user input or external execution was not provided in "
                    "time."
                ),
            )
        except asyncio.CancelledError:
            return

    def _schedule_delegation_failure(self, delegation_id: str) -> None:
        """Tell GPT-Live that an application backend is unavailable."""
        task = asyncio.create_task(
            self.model.send_delegation_result(
                delegation_id,
                "The delegated task could not be completed because the "
                "session chat agent is unavailable.",
            ),
            name=f"live-delegation-failure-{delegation_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _drive_client_delegation(
        self,
        delegation: _ClientDelegation,
        inputs: (
            Msg
            | UserConfirmResultEvent
            | ExternalExecutionResultEvent
            | UserInterruptEvent
        ),
        result_override: str | None = None,
    ) -> None:
        """Run a chat agent and return its verified result to GPT-Live."""
        final: Msg | None = None
        waiting_for_external_input = False
        try:
            if delegation.agent is None:
                raise RuntimeError("The delegation backend is unavailable.")
            async with self._delegation_lock:
                async for item in delegation.agent.reply_stream(
                    inputs,
                    yield_final_msg=True,
                ):
                    if isinstance(item, Msg):
                        final = item
                        continue
                    if not delegation.visible and isinstance(
                        item,
                        (
                            ReplyStartEvent,
                            ToolCallStartEvent,
                            ToolCallDeltaEvent,
                            ToolCallEndEvent,
                            ToolResultStartEvent,
                        ),
                    ):
                        delegation.buffered_events.append(item)
                    if isinstance(
                        item,
                        (
                            RequireUserConfirmEvent,
                            RequireExternalExecutionEvent,
                        ),
                    ):
                        self._sync_delegation_state(delegation)
                        self._show_delegation_interaction(
                            delegation,
                            item,
                        )
                        waiting_for_external_input = True
                    elif delegation.visible and isinstance(
                        item,
                        (
                            ToolCallStartEvent,
                            ToolCallDeltaEvent,
                            ToolCallEndEvent,
                            ToolResultStartEvent,
                            ToolResultTextDeltaEvent,
                            ToolResultDataDeltaEvent,
                            ToolResultEndEvent,
                            ReplyEndEvent,
                        ),
                    ):
                        if isinstance(
                            item,
                            (ToolResultEndEvent, ReplyEndEvent),
                        ):
                            self._sync_delegation_state(delegation)
                        self._emit(item)

            if waiting_for_external_input:
                self._schedule_delegation_timeout(delegation)
                return
            if final is None:
                raise RuntimeError(
                    "The delegation backend ended without a final result.",
                )

            self._sync_delegation_state(delegation)
            content = (
                result_override or (final.get_text_content() or "").strip()
            )
            if not content:
                content = "The delegated task completed without a text result."
            await self.model.send_delegation_result(
                delegation.delegation_id,
                content,
            )
            self._finish_client_delegation(delegation.delegation_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "RealtimeAgent: client delegation %s failed",
                delegation.delegation_id,
            )
            try:
                await self.model.send_delegation_result(
                    delegation.delegation_id,
                    "The delegated task failed. Ask the user to try again.",
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "RealtimeAgent: failed to report delegation failure",
                )
            self._finish_client_delegation(delegation.delegation_id)

    def _build_delegation_prompt(self) -> str:
        """Build a bounded task prompt from the current Live transcript."""
        lines: list[str] = []
        for role, delta, _, _ in self._live_transcript:
            label = "User" if role == "user" else "Voice assistant"
            if lines and lines[-1].startswith(f"{label}: "):
                lines[-1] = f"{lines[-1]}{delta}"
            else:
                lines.append(f"{label}: {delta}")
        transcript = "\n".join(lines)
        if len(transcript) > _HISTORY_MAX_TEXT_CHARS:
            transcript = transcript[-_HISTORY_MAX_TEXT_CHARS:]
        if not transcript.strip():
            transcript = "No transcript text is available."
        return (
            f"You are the task backend for a live voice conversation. "
            f"Use the latest transcript, the session context, and the "
            f"available tools to complete the delegated task. Transcript "
            f"fragments may contain corrections; prefer the latest user "
            f"intent. Do not claim an action succeeded until a tool confirms "
            f"it. Return verified facts, current status, and the next step in "
            f"a concise result of at most 300 words for the voice assistant "
            f"to communicate.\n\n## Live transcript\n{transcript}"
        )

    def _show_delegation_interaction(
        self,
        delegation: _ClientDelegation,
        interaction: RequireUserConfirmEvent | RequireExternalExecutionEvent,
    ) -> None:
        """Expose a delegated external-input request through the UI."""
        if not delegation.visible:
            start = next(
                (
                    event
                    for event in delegation.buffered_events
                    if isinstance(event, ReplyStartEvent)
                ),
                None,
            )
            if start is not None:
                self._emit(start)
            for event in delegation.buffered_events:
                if isinstance(
                    event,
                    (
                        ToolCallStartEvent,
                        ToolCallDeltaEvent,
                        ToolCallEndEvent,
                        ToolResultStartEvent,
                    ),
                ):
                    self._emit(event)
            delegation.visible = True
            delegation.buffered_events.clear()
        self._emit(interaction)
        for tool_call in interaction.tool_calls:
            self._delegated_tool_calls[tool_call.id] = delegation.delegation_id

    def _copy_delegation_state(self) -> AgentState:
        """Copy session state while omitting duplicated Live messages."""
        state = self.state.model_copy()
        for field_name in type(self.state).model_fields:
            if field_name != "context":
                setattr(
                    state,
                    field_name,
                    deepcopy(getattr(self.state, field_name)),
                )
        state.context = [
            message.model_copy(deep=True)
            for message in self.state.context
            if message.id not in self._live_message_ids
        ]
        return state

    def _sync_delegation_state(
        self,
        delegation: _ClientDelegation,
    ) -> None:
        """Persist delegated tool state without duplicating hidden prompts."""
        if delegation.agent is None:
            return
        backend_state = delegation.agent.state
        self.state.permission_context = (
            backend_state.permission_context.model_copy(deep=True)
        )
        self.state.tool_context = backend_state.tool_context.model_copy(
            deep=True,
        )
        self.state.tasks_context = backend_state.tasks_context.model_copy(
            deep=True,
        )
        existing = {
            message.id: index
            for index, message in enumerate(
                self.state.context,
            )
        }
        for message in backend_state.context:
            if (
                message.id in delegation.baseline_ids
                or message.id == delegation.prompt_id
            ):
                continue
            kept = [
                block.model_copy(deep=True)
                for block in message.content
                if block.type in {"tool_call", "tool_result"}
            ]
            if not kept:
                continue
            synced = message.model_copy(deep=True)
            synced.content = kept
            index = existing.get(message.id)
            if index is None:
                existing[message.id] = len(self.state.context)
                self.state.context.append(synced)
            else:
                self.state.context[index] = synced

    def _finish_client_delegation(self, delegation_id: str) -> None:
        """Drop routing state after a delegated task reaches a result."""
        delegation = self._delegations.pop(delegation_id, None)
        if delegation is not None:
            self._cancel_delegation_timeout(delegation)
        stale = [
            tool_call_id
            for tool_call_id, owner in self._delegated_tool_calls.items()
            if owner == delegation_id
        ]
        for tool_call_id in stale:
            self._delegated_tool_calls.pop(tool_call_id, None)
        if self._active_delegation_id == delegation_id:
            self._active_delegation_id = None
            self._activate_next_client_delegation()

    def _delegation_for_tool_calls(
        self,
        tool_call_ids: list[str],
    ) -> _ClientDelegation | None:
        """Resolve one active delegation owning every supplied tool call."""
        if not tool_call_ids:
            return None
        delegation_id = self._delegated_tool_calls.get(tool_call_ids[0])
        if delegation_id is None or any(
            self._delegated_tool_calls.get(tool_call_id) != delegation_id
            for tool_call_id in tool_call_ids
        ):
            return None
        if delegation_id != self._active_delegation_id:
            return None
        delegation = self._delegations.get(delegation_id)
        if delegation is None or delegation.expiring:
            return None
        return delegation

    def _append_live_transcript(
        self,
        event: me.TimelineTranscriptDeltaEvent,
    ) -> None:
        """Keep a timestamped transcript window within the prompt budget."""
        fragment = (
            event.role,
            event.delta,
            event.start_ms,
            event.end_ms,
        )
        self._live_transcript.append(fragment)
        self._live_transcript_chars += len(fragment[1])
        while self._live_transcript_chars > _HISTORY_MAX_TEXT_CHARS:
            role, text, start_ms, end_ms = self._live_transcript.popleft()
            excess = self._live_transcript_chars - _HISTORY_MAX_TEXT_CHARS
            if not self._live_transcript and len(text) > excess:
                kept = text[excess:]
                self._live_transcript.appendleft(
                    (role, kept, start_ms, end_ms),
                )
                self._live_transcript_chars -= excess
                break
            self._live_transcript_chars -= len(text)

    def _start_user_turn(self, item_id: str = "") -> None:
        """Open the user's turn as a reply of its own. A local VAD sees it
        before the provider creates an item, hence the generated id."""
        if self._user_turn_open:
            return  # providers repeat speech_started; one turn, one reply
        self._user_turn = item_id or uuid.uuid4().hex
        self._user_turn_open = True
        self._emit(
            ReplyStartEvent(
                session_id=self.state.session_id,
                reply_id=self._user_turn,
                name="user",
                role="user",
            ),
        )

    def _end_user_turn(self) -> None:
        """Close the user reply once no transcript remains pending."""
        if not self._user_turn_open:
            return
        self._user_turn_open = False
        self._emit(
            ReplyEndEvent(
                session_id=self.state.session_id,
                reply_id=self._user_turn,
                finished_reason=ReplyFinishedReason.COMPLETED,
            ),
        )

    def _on_transcription(self, event: me.InputTranscriptionEvent) -> None:
        """Record a settled user turn, merging a split one back together,
        and report its text on the user's reply."""
        turn = self.aggregator.take(event.text)
        if turn is None:
            logger.debug("RealtimeAgent: dropping %r", event.text)
            self._end_user_turn()
            self._user_turn = ""
            return

        # A transcript with no detected speech, e.g. after a reconnect,
        # gets a turn of its own.
        if not self._user_turn:
            self._start_user_turn(event.item_id)
        reply_id, block_id = self._user_turn, uuid.uuid4().hex
        self._emit(TextBlockStartEvent(reply_id=reply_id, block_id=block_id))
        self._emit(
            TextBlockDeltaEvent(
                reply_id=reply_id,
                block_id=block_id,
                delta=turn,
            ),
        )
        self._emit(TextBlockEndEvent(reply_id=reply_id, block_id=block_id))

        if not (
            self.aggregator.merges_with_previous() and self._merge_user(turn)
        ):
            self.state.context.append(
                UserMsg(name="user", content=turn, id=reply_id),
            )
        self._end_user_turn()
        self._user_turn = ""

    def _merge_user(self, text: str) -> bool:
        """Append *text* to the previous user turn that endpointing split.

        The stub assistant message between the two halves is dropped: it
        is whatever the model managed to say before being cut off, which
        answers half a question nobody finished asking.
        """
        context = self.state.context
        if context and context[-1].role == "assistant":
            tail = context[-1]
            if not (tail.get_text_content() or "").strip() and all(
                isinstance(b, TextBlock) for b in tail.content
            ):
                context.pop()
        if not context or context[-1].role != "user":
            return False
        previous = context[-1].get_text_content() or ""
        context[-1].content = [TextBlock(text=f"{previous}{text}")]
        return True

    def _start_reply(self, item_id: str) -> _Reply | None:
        """Open the response *item_id*, and the reply it belongs to unless
        one is waiting for it after tool results.

        Returns ``None`` for an item already closed — deltas still in
        flight after a barge-in must not reopen it.
        """
        if self._reply is not None and self._reply.item_id == item_id:
            return self._reply
        if item_id == self._finished_item:
            return None
        if self._reply is not None:
            self._finish_response()

        if not self._continuing:
            # The agent takes the turn; the reply is named by its first
            # response so that every event of the turn shares one id.
            self._reply_id = item_id
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
        self._continuing = False
        self._reply = _Reply(item_id=item_id, reply_id=self._reply_id)
        self._emit(
            ModelCallStartEvent(
                reply_id=self._reply_id,
                model_name=self.model.model,
            ),
        )
        return self._reply

    def _finish_response(self) -> None:
        """Close the open response: its blocks and the model call."""
        reply = self._reply
        if reply is None:
            return
        position = None
        if self._transport is not None:
            position = self._transport.playout()
            if position.item_id == reply.item_id:
                self._metrics.first_audio_played_at = position.first_played_at
        if reply.text_started:
            self._emit(
                TextBlockEndEvent(
                    reply_id=reply.reply_id,
                    block_id=reply.text_block_id,
                ),
            )
        if reply.audio_started:
            self._emit(
                DataBlockEndEvent(
                    reply_id=reply.reply_id,
                    block_id=reply.audio_block_id,
                ),
            )
        self._emit(
            ModelCallEndEvent(
                reply_id=reply.reply_id,
                input_tokens=self._metrics.input_tokens,
                output_tokens=self._metrics.output_tokens,
            ),
        )
        self._finished_item = reply.item_id
        self._reply = None

    def _finish_reply(self, reason: ReplyFinishedReason) -> None:
        """Close the open reply, if any, response included."""
        self._finish_response()
        self._pending_tools.clear()
        if not self._reply_id:
            return
        event = ReplyEndEvent(
            session_id=self.state.session_id,
            reply_id=self._reply_id,
            finished_reason=reason,
        )
        for message in reversed(self.state.context):
            if message.id == self._reply_id:
                message.append_event(event)
                break
        self._emit(event)
        self._reply_id = ""
        self._continuing = False

    def _emit_text(self, reply: _Reply, delta: str) -> None:
        """Emit a transcript delta, opening the block on first use."""
        if not reply.text_started:
            reply.text_started = True
            self._emit(
                TextBlockStartEvent(
                    reply_id=reply.reply_id,
                    block_id=reply.text_block_id,
                ),
            )
        # Grow the tail text block rather than appending one per delta,
        # or the context ends up as dozens of one-word blocks.
        tail = self.state.context[-1] if self.state.context else None
        blocks = (
            tail.content
            if tail is not None and tail.id == reply.reply_id
            else None
        )
        if (
            isinstance(blocks, list)
            and blocks
            and isinstance(blocks[-1], TextBlock)
        ):
            blocks[-1].text += delta
        else:
            self.state.append_context(
                self.name,
                [TextBlock(id=reply.text_block_id, text=delta)],
            )
        self._emit(
            TextBlockDeltaEvent(
                reply_id=reply.reply_id,
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
                    reply_id=reply.reply_id,
                    block_id=reply.audio_block_id,
                    media_type=media_type,
                ),
            )
        self._emit(
            DataBlockDeltaEvent(
                reply_id=reply.reply_id,
                block_id=reply.audio_block_id,
                data=base64.b64encode(pcm).decode("ascii"),
                media_type=media_type,
            ),
        )

    def _emit(
        self,
        event: AgentEvent,
        *,
        checkpoint: bool = False,
    ) -> None:
        """Queue one event for :meth:`reply_stream`."""
        snapshot = None
        if checkpoint or isinstance(
            event,
            (
                ReplyEndEvent,
                RequireExternalExecutionEvent,
                RequireUserConfirmEvent,
                ToolResultEndEvent,
            ),
        ):
            snapshot = self.state.model_copy(deep=True)
        self._out.put_nowait(_QueuedEvent(event, snapshot))

    def checkpoint_snapshot(self, event: AgentEvent) -> AgentState | None:
        """Return the immutable state paired with the yielded event."""
        checkpoint = self._yielded_checkpoint
        if checkpoint is None or checkpoint[0] != event.id:
            return None
        return checkpoint[1]

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def _schedule_tools(self, reply_id: str) -> None:
        """Run the tool calls of the finished reply, then ask for more."""
        if not reply_id or not self._pending_tools or self.toolkit is None:
            return
        calls = list(self._pending_tools.values())
        self._pending_tools.clear()
        task = asyncio.create_task(
            self._run_tools(reply_id, calls),
            name="rt-tools",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_tools(
        self,
        reply_id: str,
        calls: list[ToolCallBlock],
    ) -> None:
        """Execute *calls* in order, then trigger the follow-up response."""
        try:
            for call in calls:
                async with self._barge_lock:
                    if self._reply_id != reply_id:
                        return
                await self._run_tool(reply_id, call)
            # Unless the user cut the reply short while the tools ran.
            async with self._barge_lock:
                if self._reply_id == reply_id:
                    await self.model.request_response()
        except Exception:  # noqa: BLE001
            logger.exception("RealtimeAgent: tool execution failed")
            async with self._barge_lock:
                if self._reply_id == reply_id:
                    self._finish_reply(ReplyFinishedReason.ERROR)

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
        # The provider delivers the arguments in one piece, so a single
        # delta carries them; without it consumers rebuild an empty input.
        self._emit(
            ToolCallDeltaEvent(
                reply_id=reply_id,
                tool_call_id=call.id,
                delta=call.input,
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
        else:
            call.state = ToolCallState.ALLOWED

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
        call.state = ToolCallState.ASKING
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
        call.state = (
            ToolCallState.ALLOWED
            if result.confirmed
            else ToolCallState.FINISHED
        )
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
        call.state = ToolCallState.FINISHED
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
        block = ToolResultBlock(
            id=call.id,
            name=call.name,
            output=output,
            state=state,
        )
        # The result belongs to the reply that made the call, which may no
        # longer be the current one if the user interrupted a slow tool.
        for msg in reversed(self.state.context):
            if msg.id == reply_id:
                msg.content.append(block)
                break
        else:
            self.state.append_context(self.name, [block])
        self._emit(
            ToolResultEndEvent(
                reply_id=reply_id,
                tool_call_id=call.id,
                state=state,
            ),
        )
        await self.model.push_tool_result(block)
