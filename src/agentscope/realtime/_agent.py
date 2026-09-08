# -*- coding: utf-8 -*-
"""The realtime voice agent."""
import asyncio
import base64
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from . import _events as me
from ._aggregator import TurnAggregator, TurnAggregatorConfig
from ._base import RealtimeModelBase
from ._config import RealtimeAgentConfig
from ._metrics import TurnMetrics
from ._transport._base import (
    AudioFrame,
    ControlFrame,
    ControlFrameType,
    TransportBase,
)
from ._vad import SpeechEvent, VADBase
from .._logging import logger
from .._utils._common import _json_loads_with_repair
from ..event import (
    AgentEvent,
    ConfirmResult,
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
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
)
from ..message import (
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from ..permission import PermissionBehavior, PermissionEngine
from ..state import AgentState
from ..tool import ToolChunk, ToolResponse, Toolkit
from ..types import ReplyFinishedReason

# Sentinel closing the public event stream.
_END = object()


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
    """A voice agent: a transport on one side, a realtime model on the
    other, and the turn-taking state machine in between.

    Unlike :class:`~agentscope.agent.Agent` it is bidirectional and has no
    request/reply boundary — audio flows in continuously while events flow
    out of :meth:`events`.

    The public methods are the only entry point for discrete input. A
    transport carrying a browser's ``ControlFrame`` calls exactly those
    methods rather than reaching into the agent, so one code path handles
    the semantics whether the caller is Python or a browser. Continuous
    audio is separate and always arrives through the transport.

    Example:
        .. code-block:: python

            async with RealtimeAgent(
                name="Friday",
                sys_prompt="You are a helpful assistant.",
                model=DashScopeRealtimeModel("qwen3-omni-flash-realtime",
                                             credential=cred),
                transport=LocalAudioTransport(),
            ) as agent:
                async for event in agent.events():
                    print(event)
    """

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model: RealtimeModelBase,
        transport: TransportBase,
        toolkit: Toolkit | None = None,
        state: AgentState | None = None,
        vad: VADBase | None = None,
        config: RealtimeAgentConfig | None = None,
        aggregator: TurnAggregatorConfig | None = None,
    ) -> None:
        """Initialize the realtime agent.

        Args:
            name (`str`):
                Display name, stamped on assistant messages and events.
            sys_prompt (`str`):
                System instructions forwarded to the model on connect.
            model (`RealtimeModelBase`):
                The realtime model.
            transport (`TransportBase`):
                Where audio comes from and goes to.
            toolkit (`Toolkit | None`, optional):
                Tools available to the model. Ignored if the model card
                does not declare tool support.
            state (`AgentState | None`, optional):
                Conversation state; a new one is created if omitted.
            vad (`VADBase | None`, optional):
                Server-side VAD. Only needed when the transport does not
                report speech transitions itself.
            config (`RealtimeAgentConfig | None`, optional):
                Turn mode and fade length.
            aggregator (`TurnAggregatorConfig | None`, optional):
                Merge window and backchannel list for turn aggregation.
        """
        self.name = name
        self.sys_prompt = sys_prompt
        self.model = model
        self.transport = transport
        self.toolkit = toolkit
        self.state = state or AgentState()
        self.vad = vad
        self.config = config or RealtimeAgentConfig()
        self._aggregator = TurnAggregator(aggregator)

        self._engine = PermissionEngine(self.state.permission_context)
        self._out: asyncio.Queue = asyncio.Queue()
        self._reply: _Reply | None = None
        self._metrics = TurnMetrics()
        self._pending_tools: dict[str, ToolCallBlock] = {}
        self._confirmations: dict[str, asyncio.Future[ConfirmResult]] = {}
        self._tasks: set[asyncio.Task] = set()
        self._barge_lock = asyncio.Lock()
        self._runner: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "RealtimeAgent":
        """Connect on entry."""
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """Disconnect on exit."""
        await self.close()

    async def connect(self) -> None:
        """Open the model session and start pumping audio.

        Raises:
            `ValueError`: If the VAD and the transport disagree on the
                input sample rate, which would fail silently otherwise.
        """
        if self.vad is not None:
            if self.vad.sample_rate != self.transport.input_sample_rate:
                raise ValueError(
                    f"VAD expects {self.vad.sample_rate} Hz but the "
                    f"transport delivers "
                    f"{self.transport.input_sample_rate} Hz.",
                )
            self.vad.reset()

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
        )
        await self.transport.start()
        self._runner = asyncio.create_task(self._run(), name="rt-run")

    async def close(self) -> None:
        """Cancel everything in flight and close both ends."""
        for future in self._confirmations.values():
            future.cancel()
        self._confirmations.clear()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._runner:
            self._runner.cancel()
            await asyncio.gather(self._runner, return_exceptions=True)
        await self.transport.close()
        await self.model.close()

    async def _run(self) -> None:
        """Run both pumps until either side ends the session."""
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self._pump_uplink(), name="rt-up")
                group.create_task(self._pump_downlink(), name="rt-down")
        except* Exception:
            logger.exception("RealtimeAgent: pump failed")
        finally:
            self._out.put_nowait(_END)

    # ------------------------------------------------------------------
    # Public IO
    # ------------------------------------------------------------------

    async def events(self) -> AsyncIterator[AgentEvent]:
        """Yield agent events until the session ends."""
        while True:
            event = await self._out.get()
            if event is _END:
                return
            yield event

    async def send_text(self, text: str) -> None:
        """Send a typed user turn, cutting off any reply in progress.

        Raises:
            `NotImplementedError`: If the provider takes no text input.
        """
        if not self.model.supports_text_input:
            raise NotImplementedError(
                f"{type(self.model).__name__} accepts no text input.",
            )
        await self._barge_in()
        self.state.context.append(UserMsg(name="user", content=text))
        await self.model.push_text(text)

    def send_confirm(self, event: UserConfirmResultEvent) -> None:
        """Resolve pending tool confirmations from the user."""
        for result in event.confirm_results:
            future = self._confirmations.get(result.tool_call.id)
            if future and not future.done():
                future.set_result(result)

    async def interrupt(self) -> None:
        """Stop the active reply, as when the user presses stop."""
        await self._barge_in()

    @property
    def last_turn_metrics(self) -> TurnMetrics:
        """Latency breakdown of the most recent turn."""
        return self._metrics

    # ------------------------------------------------------------------
    # Uplink: transport -> model
    # ------------------------------------------------------------------

    async def _pump_uplink(self) -> None:
        """Forward user audio and control frames to the model."""
        async for frame in self.transport.incoming():
            if isinstance(frame, AudioFrame):
                await self._on_audio(frame)
            else:
                await self._on_control(frame)

    async def _on_audio(self, frame: AudioFrame) -> None:
        """Run VAD if we own it, then forward the audio."""
        speech = frame.speech
        if speech is None and self.vad is not None:
            speech = self.vad.push(frame.pcm)

        if speech is SpeechEvent.STARTED:
            await self._barge_in()
        elif speech is SpeechEvent.ENDED:
            self._metrics.user_speech_end_at = time.monotonic()
            if not self.model.turn_detection_enabled:
                await self.model.commit_turn()
                self._metrics.turn_committed_at = time.monotonic()

        await self.model.push_audio(frame.pcm)

    async def _on_control(self, frame: ControlFrame) -> None:
        """Handle one upstream control frame."""
        match frame.type:
            case ControlFrameType.TEXT:
                await self.send_text(frame.data.get("text", ""))
            case ControlFrameType.USER_CONFIRM:
                self.send_confirm(UserConfirmResultEvent(**frame.data))
            case ControlFrameType.INTERRUPT:
                await self._barge_in()
            case _:
                logger.debug("RealtimeAgent: ignoring %s frame", frame.type)

    # ------------------------------------------------------------------
    # Barge-in
    # ------------------------------------------------------------------

    async def _barge_in(self) -> None:
        """Cut the reply short and correct both contexts to what was heard.

        Whether a given overlap counts as an interruption is decided
        before this is called — by the provider in ``SERVER`` mode, and by
        the VAD's own debounce otherwise.

        Reached from three concurrent callers — the uplink pump, the
        downlink pump and :meth:`interrupt` — so it is serialised; the
        losers find the reply already closed and return.
        """
        async with self._barge_lock:
            await self._barge_in_locked()

    async def _barge_in_locked(self) -> None:
        """Body of :meth:`_barge_in`, run under the lock."""
        reply = self._reply
        if reply is None:
            return

        position = await self.transport.clear_audio(self.config.fade_ms)
        if position.item_id and position.item_id != reply.item_id:
            logger.warning(
                "RealtimeAgent: playout reports %s but %s is open; not "
                "truncating.",
                position.item_id,
                reply.item_id,
            )
            return
        spoken = reply.spoken_prefix(position.played_ms)

        self._truncate_reply(spoken)
        await self.model.truncate(reply.item_id, position.played_ms, spoken)
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
    # Downlink: model -> transport + events
    # ------------------------------------------------------------------

    async def _pump_downlink(self) -> None:
        """Translate model events into audio out and agent events."""
        rate = self.model.output_sample_rate
        async for event in self.model.events():
            match event:
                case me.SpeechStarted():
                    self._emit(
                        UserInputAudioStartEvent(
                            session_id=self.state.session_id,
                            item_id=event.item_id,
                        ),
                    )
                    await self._barge_in()

                case me.SpeechEnded():
                    self._metrics.user_speech_end_at = time.monotonic()

                case me.InputTranscription():
                    self._on_transcription(event)

                case me.ResponseCreated():
                    self._start_reply(event.item_id)

                case me.AudioDelta():
                    reply = self._start_reply(event.item_id)
                    reply.on_audio(event.pcm, rate)
                    await self.transport.send_audio(event.pcm, reply.item_id)
                    self._emit_audio(reply, event.pcm, rate)
                    self._metrics.backend_first_audio_at = (
                        self._metrics.backend_first_audio_at
                        or time.monotonic()
                    )

                case me.TranscriptDelta():
                    reply = self._start_reply(event.item_id)
                    reply.on_text(event.delta)
                    self._emit_text(reply, event.delta)

                case me.ToolCall():
                    self._start_reply(event.item_id)
                    self._pending_tools[event.tool_call.id] = event.tool_call

                case me.ResponseDone():
                    self._metrics.input_tokens = event.input_tokens
                    self._metrics.output_tokens = event.output_tokens
                    self._finish_reply(ReplyFinishedReason.COMPLETED)
                    self._schedule_tools()

                case me.ModelError():
                    logger.error(
                        "RealtimeAgent: model error %s: %s",
                        event.code,
                        event.message,
                    )
                    self._finish_reply(ReplyFinishedReason.ERROR)

                case me.SessionEnded():
                    return

    def _on_transcription(self, event: me.InputTranscription) -> None:
        """Record a settled user turn, merging a split one back together."""
        turn = self._aggregator.take(event.text)
        if turn is None:
            logger.debug("RealtimeAgent: dropping %r", event.text)
            return

        if self._aggregator.merges_with_previous() and self._merge_user(turn):
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

    def _start_reply(self, item_id: str) -> _Reply:
        """Open a reply for *item_id*, emitting its start events once."""
        if self._reply is not None and self._reply.item_id == item_id:
            return self._reply

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
        position = self.transport.playout()
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
        """Queue one event for :meth:`events`."""
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
        result_state = ToolResultState.SUCCESS
        try:
            async for chunk in self.toolkit.call_tool(call, self.state):
                for block in chunk.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
                        self._emit(
                            ToolResultTextDeltaEvent(
                                reply_id=reply_id,
                                tool_call_id=call.id,
                                delta=block.text,
                            ),
                        )
                if isinstance(chunk, ToolResponse):
                    result_state = chunk.state
                    break
                if not isinstance(chunk, ToolChunk):
                    break
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
