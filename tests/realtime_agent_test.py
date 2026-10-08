# -*- coding: utf-8 -*-
"""Unit tests for RealtimeAgent, driven by a scripted model and a fake
transport — no network, no sound card."""
# pylint: disable=protected-access, unused-argument
import asyncio
from typing import Any, AsyncIterator, Sequence
from unittest.async_case import IsolatedAsyncioTestCase
from utils import AnyString, MockModel

from agentscope.agent import (
    Agent,
    InjectionConfig,
    RealtimeAgent,
    TurnAggregator,
)
from agentscope.credential import DashScopeCredential
from agentscope.event import (
    DataBlockDeltaEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
)
from agentscope.message import AssistantMsg, Msg, UserMsg
from agentscope.model import ChatResponse
from agentscope.realtime import (
    AudioFrame,
    ControlFrame,
    ControlFrameType,
    ModelDisconnectedError,
    PlayoutPosition,
    RealtimeModelBase,
    RealtimeModelCard,
    SpeechTransition,
    TransportBase,
    VADBase,
)
from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    UserConfirmResultEvent,
)
from agentscope.message import TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.state import AgentState
from agentscope.tool import ToolBase, ToolChunk, ToolResponse, Toolkit
from agentscope.realtime import _events as me
from agentscope.types import ReplyFinishedReason

PCM_100MS = b"\x01\x00" * 2400


def _message_summary(message: Msg) -> dict[str, Any]:
    """Keep only state-machine fields relevant to realtime assertions."""
    payload = message.model_dump(mode="json")
    content = []
    for block in payload["content"]:
        summary = {
            key: block[key]
            for key in ("type", "text", "name", "input", "output", "state")
            if key in block
        }
        if block["type"] in {"tool_call", "tool_result"}:
            summary["id"] = block["id"]
        content.append(summary)
    return {
        "role": payload["role"],
        "id": payload["id"],
        "content": content,
        "usage": payload["usage"],
        "finished_reason": payload["finished_reason"],
    }


REPLY_R1 = [
    me.SpeechEndedEvent(item_id="u1"),
    me.InputTranscriptionEvent(item_id="u1", text="Tell me a story"),
    me.ResponseCreatedEvent(item_id="r1"),
]
for _word in [
    "Once ",
    "upon ",
    "a time, ",
    "there ",
    "was ",
    "an old ",
    "monk.",
]:
    REPLY_R1 += [
        me.TranscriptDeltaEvent(item_id="r1", delta=_word),
        me.AudioDeltaEvent(item_id="r1", pcm=PCM_100MS, sample_rate=24000),
    ]

REPLY_R2 = [
    me.InputTranscriptionEvent(item_id="u3", text="Hello"),
    me.ResponseCreatedEvent(item_id="r2"),
    me.TranscriptDeltaEvent(item_id="r2", delta="Hi there."),
    me.AudioDeltaEvent(item_id="r2", pcm=PCM_100MS, sample_rate=24000),
    me.ResponseDoneEvent(item_id="r2", input_tokens=10, output_tokens=3),
]


class ScriptedModel(RealtimeModelBase):
    """Plays one event script per session and records every call."""

    class Parameters(RealtimeModelBase.Parameters):
        """Test-only switch for delayed input transcription."""

        input_audio_transcription: bool = False

    type = "scripted"

    @property
    def input_transcription_enabled(self) -> bool:
        """Whether the scripted model delays user turn completion."""
        return self.parameters.input_audio_transcription

    def __init__(
        self,
        scripts: list[list[Any]],
        input_audio_transcription: bool = False,
    ) -> None:
        card = RealtimeModelCard(
            name="scripted",
            label="scripted",
            input_sample_rate=16000,
            output_sample_rate=24000,
        )
        super().__init__(
            "scripted",
            DashScopeCredential(api_key="sk-x"),
            parameters=self.Parameters(
                input_audio_transcription=input_audio_transcription,
            ),
            model_card=card,
        )
        self.scripts = scripts
        self.calls: list[str] = []
        self.sessions = 0
        self.instructions = ""
        self.supports_text_input = False
        self.connect_error: Exception | None = None
        self.push_text_error: Exception | None = None
        self._open = asyncio.Event()
        self._requested = asyncio.Event()

    async def connect(
        self,
        instructions: str,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> None:
        """Record a session open, its instructions and the turn-detection
        request."""
        if self.connect_error is not None:
            raise self.connect_error
        self.sessions += 1
        self._open.clear()
        self.instructions = instructions
        self.calls.append(
            f"connect(session={self.sessions},"
            f"td_off={kwargs.get('turn_detection_disabled')})",
        )

    async def close(self) -> None:
        """Record the close and release a live session."""
        self.calls.append("close")
        self._open.set()

    async def events(self) -> AsyncIterator[me.ModelEvent]:
        """Play the script for the current session."""
        script = self.scripts[self.sessions - 1]
        for event in script:
            if event == "WAIT":  # the follow-up reply after a tool call
                await self._requested.wait()
                continue
            yield event
            await asyncio.sleep(0)
        if not script or not isinstance(script[-1], me.SessionEndedEvent):
            await self._open.wait()  # a live session stays open

    async def push_audio(self, pcm: bytes) -> None:
        """Count audio pushes."""
        self.calls.append("push_audio")

    async def push_text(self, text: str) -> None:
        """Record a text turn; the agent gates on ``supports_text_input``
        before ever calling this."""
        if self.push_text_error is not None:
            raise self.push_text_error
        self.calls.append(f"push_text({text!r})")

    async def push_tool_result(self, block: ToolResultBlock) -> None:
        """Record exactly what the provider would receive."""
        self.calls.append(f"tool_result({block.id},{block.output!r})")

    async def commit_turn(self) -> None:
        """Record the commit."""
        self.calls.append("commit_turn")

    async def request_response(self) -> None:
        """Record the request and let a script waiting on it continue."""
        self.calls.append("request_response")
        self._requested.set()

    async def cancel_response(self) -> None:
        """Record the cancel."""
        self.calls.append("cancel")


class HistoryScriptedModel(ScriptedModel):
    """Records structured history supplied for each new model session."""

    supports_history_replay = True

    def __init__(self, scripts: list[list[Any]]) -> None:
        super().__init__(scripts)
        self.replayed: list[list[Msg]] = []

    async def replay_history(self, messages: Sequence[Msg]) -> None:
        """Keep a deep copy so later context changes cannot affect it."""
        replay = [message.model_copy(deep=True) for message in messages]
        self.replayed.append(replay)
        self.calls.append("replay_history")


class QueueSessionModel(ScriptedModel):
    """Keeps each session's event iterator alive on its own queue."""

    def __init__(self) -> None:
        super().__init__([])
        self.event_queues: list[asyncio.Queue[me.ModelEvent | None]] = []
        self.iterator_started: list[asyncio.Event] = []
        self.iterator_finished: list[asyncio.Event] = []

    async def connect(
        self,
        instructions: str,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> None:
        """Open a session with a distinct event queue."""
        await super().connect(instructions, tools, **kwargs)
        self.event_queues.append(asyncio.Queue())
        self.iterator_started.append(asyncio.Event())
        self.iterator_finished.append(asyncio.Event())

    async def events(self) -> AsyncIterator[me.ModelEvent]:
        """Yield only events belonging to the current session."""
        index = self.sessions - 1
        queue = self.event_queues[index]
        self.iterator_started[index].set()
        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            self.iterator_finished[index].set()


class FakeTransport(TransportBase):
    """Emit ``frames`` chunks of silence and record transport calls."""

    input_sample_rate = 16000
    output_sample_rate = 24000

    def __init__(self, frames: int) -> None:
        self.frames = frames
        self.item = ""
        self.cleared = 0
        self.started = 0
        self.closed = 0

    async def start(self) -> None:
        """Count starts; the owner is the test."""
        self.started += 1

    async def close(self) -> None:
        """Count closes."""
        self.closed += 1

    async def incoming(self) -> AsyncIterator[AudioFrame]:
        """Emit silence on a 20 ms clock."""
        for _ in range(self.frames):
            await asyncio.sleep(0.02)
            yield AudioFrame(pcm=b"\x00" * 3200)

    async def send_audio(self, pcm: bytes, item_id: str) -> None:
        """Remember which item is playing."""
        self.item = item_id

    async def clear_audio(self) -> PlayoutPosition:
        """Count cuts and report the fixed playout position."""
        self.cleared += 1
        return self.playout()

    def playout(self) -> PlayoutPosition:
        """Report a fixed position in the current item."""
        return PlayoutPosition(
            item_id=self.item,
            first_played_at=1.0,
        )


class GatedTransport(FakeTransport):
    """Stays open until ``gate`` is set, then sends its control frames, so
    a test decides when the user acts instead of a clock."""

    def __init__(self, control_frames: list[ControlFrame]) -> None:
        super().__init__(frames=0)
        self.control_frames = control_frames
        self.gate = asyncio.Event()

    async def incoming(self) -> AsyncIterator[ControlFrame]:
        """Emit the control frames once the gate opens, then end."""
        await self.gate.wait()
        for frame in self.control_frames:
            yield frame


class BlockingClearTransport(GatedTransport):
    """Hold ``clear_audio`` open while a response completion arrives."""

    def __init__(self) -> None:
        super().__init__([])
        self.audio_sent = asyncio.Event()
        self.clear_started = asyncio.Event()
        self.clear_release = asyncio.Event()

    async def send_audio(self, pcm: bytes, item_id: str) -> None:
        """Record the item and signal that interruption can begin."""
        await super().send_audio(pcm, item_id)
        self.audio_sent.set()

    async def clear_audio(self) -> PlayoutPosition:
        """Wait until the test permits the browser acknowledgment."""
        self.cleared += 1
        self.clear_started.set()
        await self.clear_release.wait()
        return self.playout()


async def _run_terminal_event_during_barge_in(
    agent: RealtimeAgent,
    model: QueueSessionModel,
    terminal_event: me.ModelEvent,
    tool_call: ToolCallBlock | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """Deliver a terminal event while browser playout clearing is blocked."""
    transport = BlockingClearTransport()
    events = []

    async def _collect() -> None:
        async for event in agent.reply_stream(transport):
            events.append(event)

    async with agent, transport:
        await model.iterator_started[0].wait()
        stream_task = asyncio.create_task(_collect())
        queue = model.event_queues[0]
        queue.put_nowait(me.ResponseCreatedEvent(item_id="r1"))
        queue.put_nowait(
            me.TranscriptDeltaEvent(
                item_id="r1",
                delta="I did not hear that.",
            ),
        )
        queue.put_nowait(
            me.AudioDeltaEvent(
                item_id="r1",
                pcm=PCM_100MS,
                sample_rate=24000,
            ),
        )
        if tool_call is not None:
            queue.put_nowait(
                me.ToolCallEvent(item_id="r1", tool_call=tool_call),
            )
        await transport.audio_sent.wait()
        while (
            tool_call is not None and tool_call.id not in agent._pending_tools
        ):
            await asyncio.sleep(0)

        interrupt_task = asyncio.create_task(agent.interrupt())
        await transport.clear_started.wait()
        queue.put_nowait(terminal_event)
        queue.put_nowait(None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        state_while_clearing = {
            "reply_open": agent._reply is not None,
            "reply_id": agent._reply_id,
            "input_tokens": agent._metrics.input_tokens,
            "pending_tools": sorted(agent._pending_tools),
        }

        transport.clear_release.set()
        await interrupt_task
        await asyncio.wait_for(model.iterator_finished[0].wait(), timeout=1)
        transport.gate.set()
        await stream_task

    return events, state_while_clearing


class EndOnSecondFrameVAD(VADBase):
    """Reports the user starting on the first chunk and stopping on the
    second."""

    sample_rate = 16000

    def __init__(self) -> None:
        self.seen = 0

    def push(self, pcm: bytes) -> SpeechTransition | None:
        """STARTED on the first chunk, ENDED on the second, else nothing."""
        self.seen += 1
        if self.seen == 1:
            return SpeechTransition.STARTED
        return SpeechTransition.ENDED if self.seen == 2 else None

    def reset(self) -> None:
        """Start counting again."""
        self.seen = 0


class RealtimeCheckpointSnapshotTest(IsolatedAsyncioTestCase):
    """Checkpoint state must stay aligned with its emitted event."""

    async def test_checkpoint_snapshot_stays_at_its_event_boundary(
        self,
    ) -> None:
        """A queued later response cannot advance an earlier checkpoint."""
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            ScriptedModel([[]]),
        )
        agent.state.context.append(
            AssistantMsg(id="r1", name="Friday", content="first"),
        )
        agent._emit(ReplyEndEvent(session_id="s1", reply_id="r1"))
        agent.state.context.append(
            AssistantMsg(id="r2", name="Friday", content="second"),
        )

        checkpoint = agent._out.get_nowait().checkpoint
        assert checkpoint is not None
        self.assertEqual(
            {
                "checkpoint": [
                    (msg.id, msg.get_text_content())
                    for msg in checkpoint.context
                ],
                "current": [
                    (msg.id, msg.get_text_content())
                    for msg in agent.state.context
                ],
            },
            {
                "checkpoint": [("r1", "first")],
                "current": [("r1", "first"), ("r2", "second")],
            },
        )


class RealtimeAgentTest(IsolatedAsyncioTestCase):
    """Behaviour of the turn-taking state machine."""

    async def _collect(
        self,
        agent: RealtimeAgent,
        transport: FakeTransport,
    ) -> list[tuple[str, Any]]:
        """Run the agent over *transport* and summarise the events: the
        user's transcripts and how each of the agent's replies ended."""
        summary: list[tuple[str, Any]] = []
        user_turns: set[str] = set()
        async with transport:
            async for event in agent.reply_stream(transport):
                if isinstance(event, ReplyStartEvent) and event.role == "user":
                    user_turns.add(event.reply_id)
                elif isinstance(event, TextBlockDeltaEvent):
                    if event.reply_id in user_turns:
                        summary.append(("user", event.delta))
                elif isinstance(event, ReplyEndEvent):
                    if event.reply_id not in user_turns:
                        summary.append(("reply_end", event.finished_reason))
        return summary

    async def test_barge_in_keeps_generated_text(self) -> None:
        """A barge-in keeps generated text and closes the active reply."""
        script = REPLY_R1 + [
            me.SpeechStartedEvent(item_id="u2"),
            me.SpeechStartedEvent(item_id="u2"),
        ]
        model = ScriptedModel([script])
        agent = RealtimeAgent("Friday", "be brief", model)
        transport = FakeTransport(frames=3)

        # Rebuild the agent's message from its events the way a client does.
        summary = []
        rebuilt = Msg(id="r1", role="assistant", name="Friday", content=[])
        async with agent, transport:
            async for event in agent.reply_stream(transport):
                if getattr(event, "reply_id", None) == "r1":
                    rebuilt.append_event(event)
                if isinstance(event, ReplyStartEvent) and event.role == "user":
                    summary.append(("user_start", event.reply_id))
                elif isinstance(event, TextBlockDeltaEvent):
                    summary.append(event.delta)
                elif isinstance(event, ReplyEndEvent):
                    summary.append(("reply_end", event.finished_reason))

        # The duplicate speech_started opens the user's turn only once.
        self.assertListEqual(
            summary,
            [
                ("user_start", "u1"),
                "Tell me a story",
                ("reply_end", "completed"),  # the user's turn
                "Once ",
                "upon ",
                "a time, ",
                "there ",
                "was ",
                "an old ",
                "monk.",
                ("user_start", "u2"),
                ("reply_end", "interrupted"),
                ("reply_end", "completed"),
            ],
        )
        self.assertEqual(
            rebuilt.get_text_content(),
            "Once upon a time, there was an old monk.",
        )
        self.assertEqual(transport.cleared, 1)
        # Audio frames interleave with model events on the transport's
        # clock, so they are counted rather than positioned.
        self.assertListEqual(
            [c for c in model.calls if c != "push_audio"],
            [
                "connect(session=1,td_off=False)",
                "cancel",
                "close",
            ],
        )
        self.assertEqual(model.calls.count("push_audio"), 3)
        self.assertListEqual(
            [(m.role, m.get_text_content()) for m in agent.state.context],
            [
                ("user", "Tell me a story"),
                ("assistant", "Once upon a time, there was an old monk."),
            ],
        )
        self.assertEqual(agent.last_turn_metrics.first_audio_played_at, 1.0)

    async def test_barge_in_after_response_done_clears_playout(self) -> None:
        """A completed response remains interruptible while its queued
        audio is still playing."""
        script = REPLY_R1 + [
            me.ResponseDoneEvent(
                item_id="r1",
                input_tokens=10,
                output_tokens=20,
            ),
            me.SpeechStartedEvent(item_id="u2"),
        ]
        model = ScriptedModel([script])
        agent = RealtimeAgent("Friday", "be brief", model)
        transport = FakeTransport(frames=3)
        rebuilt = Msg(
            id="r1",
            role="assistant",
            name="Friday",
            content=[],
        )
        async with agent, transport:
            async for event in agent.reply_stream(transport):
                if getattr(event, "reply_id", None) == "r1":
                    rebuilt.append_event(event)

        self.assertEqual(
            rebuilt.get_text_content(),
            "Once upon a time, there was an old monk.",
        )
        self.assertEqual(transport.cleared, 1)
        self.assertListEqual(
            [call for call in model.calls if call != "push_audio"],
            [
                "connect(session=1,td_off=False)",
                "close",
            ],
        )
        self.assertListEqual(
            [
                (
                    message.role,
                    message.get_text_content(),
                    message.finished_reason,
                )
                for message in agent.state.context
            ],
            [
                ("user", "Tell me a story", None),
                (
                    "assistant",
                    "Once upon a time, there was an old monk.",
                    ReplyFinishedReason.COMPLETED,
                ),
            ],
        )

    async def test_stale_response_done_does_not_run_pending_tools(
        self,
    ) -> None:
        """A completion queued behind barge-in cannot start its tools."""
        model = QueueSessionModel()
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            model,
            toolkit=Toolkit(tools=[StreamTool()]),
        )
        (
            events,
            state_while_clearing,
        ) = await _run_terminal_event_during_barge_in(
            agent,
            model,
            me.ResponseDoneEvent(
                item_id="r1",
                input_tokens=7,
                output_tokens=2,
            ),
            ToolCallBlock(
                id="c1",
                name="stream_tool",
                input='{"q": "x"}',
            ),
        )

        terminal_events = [
            event.model_dump(
                mode="json",
                exclude={"id", "created_at"},
            )
            for event in events
            if isinstance(event, (TextBlockEndEvent, ReplyEndEvent))
        ]
        self.assertEqual(
            {
                "state_while_clearing": state_while_clearing,
                "terminal_events": terminal_events,
                "context": [
                    {
                        "id": message.id,
                        "finished_reason": message.finished_reason,
                        "content": [
                            block.model_dump(
                                mode="json",
                                exclude={"created_at", "finished_at"},
                            )
                            for block in message.content
                        ],
                    }
                    for message in agent.state.context
                ],
                "input_tokens": agent._metrics.input_tokens,
                "pending_tools": sorted(agent._pending_tools),
                "model_calls": [
                    call for call in model.calls if call != "push_audio"
                ],
            },
            {
                "state_while_clearing": {
                    "reply_open": True,
                    "reply_id": "r1",
                    "input_tokens": 0,
                    "pending_tools": ["c1"],
                },
                "terminal_events": [
                    {
                        "type": "TEXT_BLOCK_END",
                        "metadata": {},
                        "reply_id": "r1",
                        "block_id": AnyString(),
                        "text": None,
                    },
                    {
                        "type": "REPLY_END",
                        "metadata": {},
                        "session_id": AnyString(),
                        "reply_id": "r1",
                        "finished_reason": "interrupted",
                        "error": None,
                    },
                ],
                "context": [
                    {
                        "id": "r1",
                        "finished_reason": "interrupted",
                        "content": [
                            {
                                "type": "text",
                                "text": "I did not hear that.",
                                "id": AnyString(),
                            },
                            {
                                "type": "tool_call",
                                "id": "c1",
                                "name": "stream_tool",
                                "input": '{"q": "x"}',
                                "state": "pending",
                                "suggested_rules": [],
                            },
                        ],
                    },
                ],
                "input_tokens": 0,
                "pending_tools": [],
                "model_calls": [
                    "connect(session=1,td_off=False)",
                    "cancel",
                    "close",
                ],
            },
        )

    async def test_interrupt_stops_active_reply(self) -> None:
        """``interrupt()`` stops generation but preserves its text."""
        model = ScriptedModel([REPLY_R1])
        agent = RealtimeAgent("Friday", "be brief", model)
        transport = GatedTransport([])

        reply_ends = []
        async with agent, transport:
            async for event in agent.reply_stream(transport):
                if isinstance(event, TextBlockDeltaEvent):
                    if event.delta == "monk.":
                        await agent.interrupt()
                        transport.gate.set()
                elif isinstance(event, ReplyEndEvent):
                    reply_ends.append((event.reply_id, event.finished_reason))

        self.assertListEqual(
            reply_ends,
            [("u1", "completed"), ("r1", "interrupted")],
        )
        self.assertEqual(transport.cleared, 1)
        self.assertListEqual(
            [(m.role, m.get_text_content()) for m in agent.state.context],
            [
                ("user", "Tell me a story"),
                ("assistant", "Once upon a time, there was an old monk."),
            ],
        )

    async def test_interrupt_frame_stops_active_reply(self) -> None:
        """An INTERRUPT control frame cuts the reply in flight."""
        model = ScriptedModel([REPLY_R1])
        agent = RealtimeAgent("Friday", "be brief", model)
        transport = GatedTransport(
            [ControlFrame(type=ControlFrameType.INTERRUPT)],
        )

        reply_ends = []
        async with agent, transport:
            async for event in agent.reply_stream(transport):
                if isinstance(event, TextBlockDeltaEvent):
                    if event.delta == "monk.":
                        transport.gate.set()
                elif isinstance(event, ReplyEndEvent):
                    reply_ends.append((event.reply_id, event.finished_reason))

        self.assertListEqual(
            reply_ends,
            [("u1", "completed"), ("r1", "interrupted")],
        )
        self.assertListEqual(
            model.calls,
            [
                "connect(session=1,td_off=False)",
                "cancel",
                "close",
            ],
        )

    async def test_provider_timeout_reconnects_on_next_audio(self) -> None:
        """When the provider closes the session, nothing reconnects until
        the next user audio, which reconnects with the current context."""
        model = ScriptedModel(
            [
                REPLY_R1 + [me.SessionEndedEvent(reason="idle")],
                REPLY_R2,
            ],
        )
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            model,
            aggregator=TurnAggregator(merge_window_ms=0),
        )

        async with agent:
            # No transport, no audio: the provider times the session out
            # and nothing reconnects.
            await asyncio.sleep(0.1)
            self.assertFalse(agent._connected)  # pylint: disable=W0212
            self.assertEqual(model.sessions, 1)

            summary = await self._collect(agent, FakeTransport(frames=2))

        self.assertEqual(model.sessions, 2)
        # Reconnection starts with the context available when the previous
        # model session ended.
        self.assertIn("connect(session=2,td_off=False)", model.calls)
        self.assertEqual(
            model.instructions,
            f"{agent.system_prompt}\n\n## Conversation so far\n"
            f"user: Tell me a story\n{agent.name}: Once ",
        )
        # Events produced while no run was active are delivered first;
        # a reply streamed with nobody listening is cut off exactly once.
        self.assertListEqual(
            summary,
            [
                ("user", "Tell me a story"),
                ("reply_end", "interrupted"),
                ("user", "Hello"),
                ("reply_end", "completed"),
            ],
        )
        self.assertListEqual(
            [(m.role, m.get_text_content()) for m in agent.state.context],
            [
                ("user", "Tell me a story"),
                ("assistant", "Once "),
                ("user", "Hello"),
                ("assistant", "Hi there."),
            ],
        )

    async def test_structured_history_is_replayed_on_reconnect(self) -> None:
        """A replay-capable provider gets roles, summary, and no fallback."""
        state = AgentState(
            summary="Earlier summary",
            context=[
                UserMsg(name="user", content="hello"),
                AssistantMsg(
                    name="Friday",
                    content="hi",
                    finished_reason=ReplyFinishedReason.COMPLETED,
                ),
                AssistantMsg(
                    name="Friday",
                    content="partial",
                ),
                AssistantMsg(
                    name="Friday",
                    content="legacy complete",
                    finished_at="2026-01-01T00:00:00",
                ),
                AssistantMsg(
                    name="Friday",
                    content="unfinished",
                    finished_reason=ReplyFinishedReason.INTERRUPTED,
                ),
                AssistantMsg(
                    name="Friday",
                    content=[
                        TextBlock(text="calling a tool"),
                        ToolCallBlock(
                            id="call-1",
                            name="lookup",
                            input="{}",
                        ),
                    ],
                    finished_reason=ReplyFinishedReason.INTERRUPTED,
                ),
            ],
        )
        model = HistoryScriptedModel(
            [
                [me.SessionEndedEvent(reason="idle")],
                [],
            ],
        )
        agent = RealtimeAgent("Friday", "be brief", model, state=state)

        async with agent:
            await asyncio.sleep(0.1)
            disconnected_before_reconnect = not agent._connected
            await self._collect(agent, FakeTransport(frames=1))

        replayed = [
            [
                (
                    message.role,
                    message.name,
                    message.get_text_content(),
                    message.model_dump(mode="json")["finished_reason"],
                )
                for message in replay
            ]
            for replay in model.replayed
        ]
        expected_replay = [
            ("system", "summary", "Earlier summary", None),
            ("user", "user", "hello", None),
            ("assistant", "Friday", "hi", "completed"),
            ("assistant", "Friday", "partial", None),
            ("assistant", "Friday", "legacy complete", None),
            ("assistant", "Friday", "unfinished", "interrupted"),
            ("assistant", "Friday", "calling a tool", "interrupted"),
        ]
        self.assertEqual(
            {
                "disconnected_before_reconnect": (
                    disconnected_before_reconnect
                ),
                "instructions": model.instructions,
                "sessions": model.sessions,
                "calls": model.calls,
                "replayed": replayed,
            },
            {
                "disconnected_before_reconnect": True,
                "instructions": "be brief",
                "sessions": 2,
                "calls": [
                    "connect(session=1,td_off=False)",
                    "replay_history",
                    "connect(session=2,td_off=False)",
                    "replay_history",
                    "push_audio",
                    "close",
                ],
                "replayed": [expected_replay, expected_replay],
            },
        )

    async def test_completed_chat_reply_is_replayed_in_realtime(self) -> None:
        """Switching modes preserves the complete preceding chat turn."""
        story = (
            "A small cat waited by the window every day for its friend "
            "to return."
        )
        chat_model = MockModel()
        chat_model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text=story)],
                    is_last=True,
                ),
            ],
        )
        chat_agent = Agent(
            name="Friday",
            system_prompt="be brief",
            model=chat_model,
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        await chat_agent.reply(
            UserMsg(name="user", content="Tell me a short story"),
        )

        realtime_model = HistoryScriptedModel([[]])
        realtime_agent = RealtimeAgent(
            "Friday",
            "be brief",
            realtime_model,
            state=chat_agent.state,
        )
        async with realtime_agent:
            replayed = [
                message.model_dump(mode="json")
                for message in realtime_model.replayed[0]
            ]

        fallback_model = ScriptedModel([[]])
        fallback_agent = RealtimeAgent(
            "Friday",
            "be brief",
            fallback_model,
            state=chat_agent.state,
        )
        async with fallback_agent:
            instructions = fallback_model.instructions

        self.assertDictEqual(
            {
                "replayed": replayed,
                "fallback_instructions": instructions,
            },
            {
                "replayed": [
                    message.model_dump(mode="json")
                    for message in chat_agent.state.context
                ],
                "fallback_instructions": (
                    "be brief\n\n## Conversation so far\n"
                    "user: Tell me a short story\n"
                    f"Friday: {story}"
                ),
            },
        )

    async def test_history_fallback_keeps_only_recent_whole_messages(
        self,
    ) -> None:
        """Text fallback drops old messages instead of slicing one in half."""
        old_text = "o" * 20_000
        recent_text = "n" * 20_000
        model = ScriptedModel([[]])
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            model,
            state=AgentState(
                context=[
                    UserMsg(name="old", content=old_text),
                    UserMsg(name="recent", content=recent_text),
                ],
            ),
        )
        async with agent:
            instructions = model.instructions

        fallback = instructions.removeprefix(
            "be brief\n\n## Conversation so far\n",
        )

        self.assertDictEqual(
            {
                "fallback": fallback,
                "contains_old_message": old_text in fallback,
                "length": len(fallback),
            },
            {
                "fallback": f"recent: {recent_text}",
                "contains_old_message": False,
                "length": len("recent: ") + len(recent_text),
            },
        )

    async def test_old_downlink_cannot_disconnect_new_session(self) -> None:
        """Late completion of an old iterator leaves reconnection active."""
        model = QueueSessionModel()
        agent = RealtimeAgent("Friday", "be brief", model)

        async with agent:
            await model.iterator_started[0].wait()
            old_queue = model.event_queues[0]
            old_queue.put_nowait(me.SpeechStartedEvent(item_id="u1"))
            old_queue.put_nowait(me.ResponseCreatedEvent(item_id="r1"))
            old_queue.put_nowait(
                me.TranscriptDeltaEvent(item_id="r1", delta="partial"),
            )
            while agent._reply is None:  # pylint: disable=W0212
                await asyncio.sleep(0)
            agent._mark_disconnected()  # pylint: disable=protected-access
            await agent.connect()
            old_queue.put_nowait(None)
            await model.iterator_finished[0].wait()
            await model.iterator_started[1].wait()

            active_state = {
                "connected": agent._connected,  # pylint: disable=W0212
                "connection_generation": agent._connection_generation,
                "sessions": model.sessions,
                "reply_open": agent._reply is not None,
                "user_turn_open": agent._user_turn_open,
                "iterator_started": [
                    event.is_set() for event in model.iterator_started
                ],
                "iterator_finished": [
                    event.is_set() for event in model.iterator_finished
                ],
            }

        self.assertDictEqual(
            {
                "active": active_state,
                "closed": {
                    "connected": agent._connected,
                    "iterator_finished": [
                        event.is_set() for event in model.iterator_finished
                    ],
                    "calls": model.calls,
                },
            },
            {
                "active": {
                    "connected": True,
                    "connection_generation": 2,
                    "sessions": 2,
                    "reply_open": False,
                    "user_turn_open": False,
                    "iterator_started": [True, True],
                    "iterator_finished": [True, False],
                },
                "closed": {
                    "connected": False,
                    "iterator_finished": [True, True],
                    "calls": [
                        "connect(session=1,td_off=False)",
                        "connect(session=2,td_off=False)",
                        "close",
                    ],
                },
            },
        )

    async def test_provider_timeout_reconnects_on_text(self) -> None:
        """Text reconnects a timed-out provider without duplicating the
        new turn in the reconnect instructions."""
        model = ScriptedModel(
            [
                [me.SessionEndedEvent(reason="idle")],
                [],
            ],
        )
        model.supports_text_input = True
        agent = RealtimeAgent("Friday", "be brief", model)

        async with agent:
            await asyncio.sleep(0.1)
            self.assertFalse(agent._connected)  # pylint: disable=W0212
            await agent.send("hello")

        self.assertEqual(model.instructions, "be brief")
        self.assertListEqual(
            model.calls,
            [
                "connect(session=1,td_off=False)",
                "connect(session=2,td_off=False)",
                "push_text('hello')",
                "close",
            ],
        )
        self.assertListEqual(
            [(m.role, m.get_text_content()) for m in agent.state.context],
            [("user", "hello")],
        )

    async def test_failed_text_delivery_does_not_change_context(self) -> None:
        """A disconnect while sending text leaves no undelivered turn."""
        model = ScriptedModel([[]])
        model.supports_text_input = True
        agent = RealtimeAgent("Friday", "be brief", model)
        error = ModelDisconnectedError("Not connected.")

        async with agent:
            model.push_text_error = error
            with self.assertRaisesRegex(
                ModelDisconnectedError,
                "Not connected",
            ):
                await agent._on_control(  # pylint: disable=W0212
                    ControlFrame(
                        type=ControlFrameType.TEXT,
                        data={"text": "hello"},
                    ),
                )
            self.assertFalse(agent._connected)  # pylint: disable=W0212

        self.assertListEqual(agent.state.context, [])

    async def test_failed_text_reconnect_does_not_change_context(self) -> None:
        """A failed reconnect leaves no undelivered text turn."""
        model = ScriptedModel(
            [[me.SessionEndedEvent(reason="idle")]],
        )
        model.supports_text_input = True
        agent = RealtimeAgent("Friday", "be brief", model)

        async with agent:
            await asyncio.sleep(0.1)
            model.connect_error = RuntimeError("reconnect failed")
            with self.assertRaisesRegex(
                ModelDisconnectedError,
                "Provider unreachable",
            ):
                await agent.send("hello")

        self.assertListEqual(agent.state.context, [])

    async def test_local_vad_owns_turns(self) -> None:
        """Passing a VAD disables provider turn detection, reports the
        user's speech as events and commits the turn when the VAD reports
        the user stopped."""
        model = ScriptedModel([[]])
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            model,
            vad=EndOnSecondFrameVAD(),
        )
        speech = []
        async with agent:
            transport = FakeTransport(frames=3)
            async with transport:
                async for event in agent.reply_stream(transport):
                    if isinstance(event, ReplyStartEvent):
                        speech.append((event.type, event.role, event.reply_id))
                    elif isinstance(event, ReplyEndEvent):
                        speech.append(
                            (
                                event.type,
                                event.finished_reason,
                                event.reply_id,
                            ),
                        )

        # The user's turn is a reply of its own, with a locally generated id
        # since no provider item exists yet.
        self.assertListEqual(
            speech,
            [
                ("REPLY_START", "user", AnyString()),
                ("REPLY_END", "completed", AnyString()),
            ],
        )
        self.assertEqual(speech[0][2], speech[1][2])
        self.assertListEqual(
            model.calls,
            [
                "connect(session=1,td_off=True)",
                "push_audio",
                "commit_turn",
                "request_response",
                "push_audio",
                "push_audio",
                "close",
            ],
        )

    async def test_run_exit_cancels_reply_in_flight(self) -> None:
        """The transport ending mid-reply cancels the reply so the model
        does not keep talking to nobody."""
        model = ScriptedModel([REPLY_R1])  # never sends response.done
        agent = RealtimeAgent("Friday", "be brief", model)
        async with agent:
            summary = await self._collect(agent, FakeTransport(frames=1))

        self.assertListEqual(
            summary,
            [("user", "Tell me a story"), ("reply_end", "interrupted")],
        )
        self.assertIn("cancel", model.calls)

    async def test_text_rejected_by_audio_only_model(self) -> None:
        """A provider without text input refuses typed turns."""
        agent = RealtimeAgent("Friday", "be brief", ScriptedModel([[]]))
        with self.assertRaises(NotImplementedError):
            await agent.send("hi")

    async def test_backchannel_is_dropped(self) -> None:
        """A bare acknowledgement never becomes a turn."""
        model = ScriptedModel(
            [[me.InputTranscriptionEvent(item_id="u1", text="okay.")]],
        )
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            model,
            aggregator=TurnAggregator(backchannels=frozenset({"okay"})),
        )
        async with agent:
            summary = await self._collect(agent, FakeTransport(frames=1))

        self.assertListEqual(summary, [])
        self.assertListEqual(agent.state.context, [])


class RealtimeAgentTranscriptionTest(IsolatedAsyncioTestCase):
    """Verify user reply boundaries around delayed transcription."""

    async def test_user_reply_ends_after_transcription_result(self) -> None:
        """Success and failure both close their delayed user replies."""
        model = ScriptedModel(
            [
                [
                    me.SpeechStartedEvent(item_id="u1"),
                    me.SpeechEndedEvent(item_id="u1"),
                    me.ResponseCreatedEvent(item_id="r1"),
                    me.InputTranscriptionEvent(item_id="u1", text="hello"),
                    me.ResponseDoneEvent(item_id="r1"),
                    me.SpeechStartedEvent(item_id="u2"),
                    me.SpeechEndedEvent(item_id="u2"),
                    me.InputTranscriptionFailedEvent(item_id="u2"),
                ],
            ],
            input_audio_transcription=True,
        )
        agent = RealtimeAgent("Friday", "be brief", model)
        user_events = []

        async with agent:
            transport = FakeTransport(frames=3)
            async with transport:
                async for event in agent.reply_stream(transport):
                    if getattr(event, "role", None) == "user" or getattr(
                        event,
                        "reply_id",
                        None,
                    ) in {"u1", "u2"}:
                        user_events.append(
                            (
                                event.type,
                                getattr(event, "role", None),
                                getattr(event, "delta", None),
                                getattr(event, "finished_reason", None),
                            ),
                        )

        self.assertEqual(
            {
                "events": user_events,
                "context": [
                    (message.role, message.get_text_content())
                    for message in agent.state.context
                ],
            },
            {
                "events": [
                    ("REPLY_START", "user", None, None),
                    ("TEXT_BLOCK_START", None, None, None),
                    ("TEXT_BLOCK_DELTA", None, "hello", None),
                    ("TEXT_BLOCK_END", None, None, None),
                    ("REPLY_END", None, None, "completed"),
                    ("REPLY_START", "user", None, None),
                    ("REPLY_END", None, None, "completed"),
                ],
                "context": [("user", "hello")],
            },
        )


class RealtimeAgentLifecycleTest(IsolatedAsyncioTestCase):
    """Verify interruption and terminal events are synchronized."""

    async def test_model_error_waits_for_barge_in_reconciliation(self) -> None:
        """A terminal error waits for an in-progress interruption."""
        model = QueueSessionModel()
        agent = RealtimeAgent("Friday", "be brief", model)
        (
            events,
            state_while_clearing,
        ) = await _run_terminal_event_during_barge_in(
            agent,
            model,
            me.ModelErrorEvent(code="provider_error", message="failed"),
        )

        self.assertEqual(
            {
                "state_while_clearing": state_while_clearing,
                "terminal_events": [
                    (
                        event.type,
                        event.text
                        if isinstance(event, TextBlockEndEvent)
                        else event.finished_reason,
                    )
                    for event in events
                    if isinstance(event, (TextBlockEndEvent, ReplyEndEvent))
                ],
                "context": [
                    _message_summary(message)
                    for message in agent.state.context
                ],
                "model_calls": [
                    call for call in model.calls if call != "push_audio"
                ],
            },
            {
                "state_while_clearing": {
                    "reply_open": True,
                    "reply_id": "r1",
                    "input_tokens": 0,
                    "pending_tools": [],
                },
                "terminal_events": [
                    ("TEXT_BLOCK_END", None),
                    ("REPLY_END", ReplyFinishedReason.INTERRUPTED),
                ],
                "context": [
                    {
                        "role": "assistant",
                        "id": "r1",
                        "content": [
                            {
                                "type": "text",
                                "text": "I did not hear that.",
                            },
                        ],
                        "usage": None,
                        "finished_reason": "interrupted",
                    },
                ],
                "model_calls": [
                    "connect(session=1,td_off=False)",
                    "cancel",
                    "close",
                ],
            },
        )


class StreamTool(ToolBase):
    """Streams two chunks, then the completed result."""

    name: str = "stream_tool"
    description: str = "streams"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Run freely."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="test",
            message="test",
        )

    async def __call__(self, q: str, **kwargs: Any) -> Any:
        """Yield chunks then the final response."""
        yield ToolChunk(content=[TextBlock(text=f"{q}-a")])
        yield ToolChunk(content=[TextBlock(text=f"{q}-b")])
        yield ToolResponse(content=[TextBlock(text=f"{q}-final")])


class BlockingTool(StreamTool):
    """Wait until the test releases a tool already in progress."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def __call__(self, q: str, **kwargs: Any) -> Any:
        """Hold the tool result until a barge-in has completed."""
        await self.release.wait()
        yield ToolResponse(content=[TextBlock(text=f"{q}-final")])


class AskTool(StreamTool):
    """Requires user confirmation before running. Not read-only, or the
    permission engine's read-only fast path would allow it unasked."""

    name: str = "ask_tool"
    is_read_only: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always ask."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            decision_reason="test",
            message="test",
        )


class BrokenTool(StreamTool):
    """Raises while running."""

    name: str = "broken_tool"

    async def __call__(self, q: str, **kwargs: Any) -> Any:
        """Fail."""
        raise RuntimeError("boom")
        yield  # pylint: disable=unreachable


def _tool_script(name: str) -> list[me.ModelEvent]:
    """A reply that only calls *name* and completes."""
    return [
        me.ResponseCreatedEvent(item_id="r1"),
        me.ToolCallEvent(
            item_id="r1",
            tool_call=ToolCallBlock(id="c1", name=name, input='{"q": "x"}'),
        ),
        me.ResponseDoneEvent(item_id="r1"),
    ]


class RealtimeAgentToolTest(IsolatedAsyncioTestCase):
    """Tool calls: permission, execution, result delivery."""

    async def _run_tool_scenario(
        self,
        tool: ToolBase,
        confirm: bool | None = None,
    ) -> tuple[list[tuple[Any, ...]], ScriptedModel]:
        """Run one tool-calling reply; answer a permission prompt with
        *confirm* if one appears. Returns the tool-related events."""
        model = ScriptedModel([_tool_script(tool.name)])
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            model,
            toolkit=Toolkit(tools=[tool]),
        )

        def _checkpoint_tool_state(event: Any) -> str:
            checkpoint = agent.checkpoint_snapshot(event)
            if checkpoint is None:
                self.fail("The tool lifecycle event has no checkpoint.")
            tool_calls = checkpoint.context[-1].get_content_blocks(
                "tool_call",
            )
            self.assertEqual([call.id for call in tool_calls], ["c1"])
            return str(tool_calls[0].state)

        events: list[tuple[Any, ...]] = []
        async with agent:
            transport = FakeTransport(frames=4)
            async with transport:
                async for event in agent.reply_stream(transport):
                    match event:
                        case ToolCallStartEvent():
                            events.append(("call_start", event.tool_call_name))
                        case ToolCallEndEvent():
                            events.append(("call_end", event.tool_call_id))
                        case RequireUserConfirmEvent():
                            events.append(
                                (
                                    "ask",
                                    [c.name for c in event.tool_calls],
                                    _checkpoint_tool_state(event),
                                ),
                            )
                            await agent.send(
                                UserConfirmResultEvent(
                                    reply_id=event.reply_id,
                                    confirm_results=[
                                        ConfirmResult(
                                            tool_call=event.tool_calls[0],
                                            confirmed=bool(confirm),
                                        ),
                                    ],
                                ),
                            )
                        case ToolResultStartEvent():
                            events.append(
                                ("result_start", event.tool_call_name),
                            )
                        case ToolResultTextDeltaEvent():
                            events.append(("delta", event.delta))
                        case ToolResultEndEvent():
                            events.append(
                                (
                                    "result_end",
                                    event.state,
                                    _checkpoint_tool_state(event),
                                ),
                            )
        return events, model

    async def test_streamed_tool_result_is_not_duplicated(self) -> None:
        """Chunks are shown as they come; the provider gets only the
        completed result, once."""
        events, model = await self._run_tool_scenario(StreamTool())

        self.assertListEqual(
            events,
            [
                ("call_start", "stream_tool"),
                ("call_end", "c1"),
                ("result_start", "stream_tool"),
                ("delta", "x-a"),
                ("delta", "x-b"),
                ("result_end", "success", "finished"),
            ],
        )
        self.assertListEqual(
            [c for c in model.calls if not c.startswith("push_audio")],
            [
                "connect(session=1,td_off=False)",
                "tool_result(c1,'x-final')",
                "request_response",
                "close",
            ],
        )

    async def test_confirmed_tool_runs(self) -> None:
        """A confirmed permission prompt lets the tool run."""
        events, model = await self._run_tool_scenario(AskTool(), confirm=True)

        self.assertListEqual(
            events,
            [
                ("call_start", "ask_tool"),
                ("call_end", "c1"),
                ("ask", ["ask_tool"], "asking"),
                ("result_start", "ask_tool"),
                ("delta", "x-a"),
                ("delta", "x-b"),
                ("result_end", "success", "finished"),
            ],
        )
        self.assertIn("tool_result(c1,'x-final')", model.calls)

    async def test_denied_tool_reports_denial(self) -> None:
        """A refused prompt sends a denial to the provider, runs nothing."""
        events, model = await self._run_tool_scenario(AskTool(), confirm=False)

        self.assertListEqual(
            events,
            [
                ("call_start", "ask_tool"),
                ("call_end", "c1"),
                ("ask", ["ask_tool"], "asking"),
                ("result_start", "ask_tool"),
                ("delta", 'Tool "ask_tool" denied by user.'),
                ("result_end", "denied", "finished"),
            ],
        )
        self.assertIn(
            "tool_result(c1,'Tool \"ask_tool\" denied by user.')",
            model.calls,
        )

    async def test_failing_tool_reports_error(self) -> None:
        """The toolkit turns an exception into an error response, which
        is forwarded as-is."""
        events, model = await self._run_tool_scenario(BrokenTool())

        self.assertListEqual(
            events,
            [
                ("call_start", "broken_tool"),
                ("call_end", "c1"),
                ("result_start", "broken_tool"),
                ("delta", "boom"),
                ("result_end", "error", "finished"),
            ],
        )
        self.assertIn("tool_result(c1,'boom')", model.calls)

    async def test_tool_call_arguments_reach_the_event_stream(self) -> None:
        """A client rebuilding the reply from the event stream sees the
        tool call's arguments, not an empty input."""
        model = ScriptedModel([_tool_script("stream_tool")])
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            model,
            toolkit=Toolkit(tools=[StreamTool()]),
        )
        rebuilt = Msg(id="r1", role="assistant", name="Friday", content=[])
        async with agent:
            transport = FakeTransport(frames=4)
            async with transport:
                async for event in agent.reply_stream(transport):
                    if getattr(event, "reply_id", None) == "r1":
                        rebuilt.append_event(event)

        self.assertListEqual(
            [b.model_dump() for b in rebuilt.content],
            [
                {
                    "type": "tool_call",
                    "id": "c1",
                    "name": "stream_tool",
                    "input": '{"q": "x"}',
                    "state": "finished",
                    "suggested_rules": [],
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                },
                {
                    "type": "tool_result",
                    "id": "c1",
                    "name": "stream_tool",
                    "output": [
                        {
                            "type": "text",
                            "text": "x-ax-b",
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "state": "success",
                    "metadata": {},
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                },
            ],
        )


class RealtimeAgentFullStreamTest(IsolatedAsyncioTestCase):
    """The complete event stream of a turn that calls a tool and then
    answers with speech, asserted as one structure."""

    async def test_tool_call_then_spoken_reply(self) -> None:
        """User asks → model speaks, calls a tool → tool runs → model
        speaks the answer. Every event, in order."""
        model = ScriptedModel(
            [
                [
                    me.SpeechEndedEvent(item_id="u1"),
                    me.InputTranscriptionEvent(
                        item_id="u1",
                        text="What is the weather?",
                    ),
                    me.ResponseCreatedEvent(item_id="r1"),
                    me.TranscriptDeltaEvent(
                        item_id="r1",
                        delta="Let me check.",
                    ),
                    me.AudioDeltaEvent(
                        item_id="r1",
                        pcm=b"\x01\x00",
                        sample_rate=24000,
                    ),
                    me.ResponseCreatedEvent(item_id="r2"),
                    me.ToolCallEvent(
                        item_id="r2",
                        tool_call=ToolCallBlock(
                            id="c1",
                            name="stream_tool",
                            input='{"q": "x"}',
                        ),
                    ),
                    me.ResponseDoneEvent(
                        item_id="r2",
                        input_tokens=5,
                        output_tokens=2,
                    ),
                    "WAIT",
                    me.ResponseCreatedEvent(item_id="r3"),
                    me.TranscriptDeltaEvent(
                        item_id="r3",
                        delta="It is sunny.",
                    ),
                    me.AudioDeltaEvent(
                        item_id="r3",
                        pcm=b"\x01\x00",
                        sample_rate=24000,
                    ),
                    me.ResponseDoneEvent(
                        item_id="r3",
                        input_tokens=9,
                        output_tokens=3,
                    ),
                ],
            ],
        )
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            model,
            toolkit=Toolkit(tools=[StreamTool()]),
        )
        events = []
        async with agent:
            transport = FakeTransport(frames=6)
            async with transport:
                async for event in agent.reply_stream(transport):
                    events.append(
                        (
                            event.type,
                            getattr(event, "reply_id", None),
                            getattr(event, "role", None),
                            getattr(event, "delta", None),
                            getattr(event, "tool_call_id", None),
                            getattr(event, "finished_reason", None),
                        ),
                    )

        self.assertListEqual(
            events,
            [
                ("REPLY_START", "u1", "user", None, None, None),
                ("TEXT_BLOCK_START", "u1", None, None, None, None),
                (
                    "TEXT_BLOCK_DELTA",
                    "u1",
                    None,
                    "What is the weather?",
                    None,
                    None,
                ),
                ("TEXT_BLOCK_END", "u1", None, None, None, None),
                ("REPLY_END", "u1", None, None, None, "completed"),
                ("REPLY_START", "r1", "assistant", None, None, None),
                ("MODEL_CALL_START", "r1", None, None, None, None),
                ("TEXT_BLOCK_START", "r1", None, None, None, None),
                (
                    "TEXT_BLOCK_DELTA",
                    "r1",
                    None,
                    "Let me check.",
                    None,
                    None,
                ),
                ("DATA_BLOCK_START", "r1", None, None, None, None),
                ("DATA_BLOCK_DELTA", "r1", None, None, None, None),
                ("TEXT_BLOCK_END", "r1", None, None, None, None),
                ("DATA_BLOCK_END", "r1", None, None, None, None),
                ("MODEL_CALL_END", "r1", None, None, None, "completed"),
                ("MODEL_CALL_START", "r1", None, None, None, None),
                ("MODEL_CALL_END", "r1", None, None, None, "completed"),
                ("TOOL_CALL_START", "r1", None, None, "c1", None),
                (
                    "TOOL_CALL_DELTA",
                    "r1",
                    None,
                    '{"q": "x"}',
                    "c1",
                    None,
                ),
                ("TOOL_CALL_END", "r1", None, None, "c1", None),
                ("TOOL_RESULT_START", "r1", None, None, "c1", None),
                (
                    "TOOL_RESULT_TEXT_DELTA",
                    "r1",
                    None,
                    "x-a",
                    "c1",
                    None,
                ),
                (
                    "TOOL_RESULT_TEXT_DELTA",
                    "r1",
                    None,
                    "x-b",
                    "c1",
                    None,
                ),
                ("TOOL_RESULT_END", "r1", None, None, "c1", None),
                ("MODEL_CALL_START", "r1", None, None, None, None),
                ("TEXT_BLOCK_START", "r1", None, None, None, None),
                (
                    "TEXT_BLOCK_DELTA",
                    "r1",
                    None,
                    "It is sunny.",
                    None,
                    None,
                ),
                ("DATA_BLOCK_START", "r1", None, None, None, None),
                ("DATA_BLOCK_DELTA", "r1", None, None, None, None),
                ("TEXT_BLOCK_END", "r1", None, None, None, None),
                ("DATA_BLOCK_END", "r1", None, None, None, None),
                ("MODEL_CALL_END", "r1", None, None, None, "completed"),
                ("REPLY_END", "r1", None, None, None, "completed"),
            ],
        )
        # The context records the whole turn as one assistant message: the
        # first words, the tool call and its result, then the spoken answer.
        self.assertEqual(
            [_message_summary(message) for message in agent.state.context],
            [
                {
                    "role": "user",
                    "id": "u1",
                    "content": [
                        {"type": "text", "text": "What is the weather?"},
                    ],
                    "usage": None,
                    "finished_reason": None,
                },
                {
                    "role": "assistant",
                    "id": "r1",
                    "content": [
                        {"type": "text", "text": "Let me check."},
                        {
                            "type": "tool_call",
                            "name": "stream_tool",
                            "input": '{"q": "x"}',
                            "state": "finished",
                            "id": "c1",
                        },
                        {
                            "type": "tool_result",
                            "name": "stream_tool",
                            "output": "x-final",
                            "state": "success",
                            "id": "c1",
                        },
                        {"type": "text", "text": "It is sunny."},
                    ],
                    "usage": {
                        "input_tokens": 14,
                        "output_tokens": 5,
                        "cache_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    "finished_reason": "completed",
                },
            ],
        )
        self.assertListEqual(
            [c for c in model.calls if c != "push_audio"],
            [
                "connect(session=1,td_off=False)",
                "tool_result(c1,'x-final')",
                "request_response",
                "close",
            ],
        )

    async def test_barge_in_during_followup_keeps_generated_text(
        self,
    ) -> None:
        """Barge-in keeps text from every response in the reply."""
        model = ScriptedModel(
            [
                [
                    me.SpeechEndedEvent(item_id="u1"),
                    me.InputTranscriptionEvent(
                        item_id="u1",
                        text="What is the weather?",
                    ),
                    me.ResponseCreatedEvent(item_id="r1"),
                    me.TranscriptDeltaEvent(
                        item_id="r1",
                        delta="Let me check.",
                    ),
                    me.AudioDeltaEvent(
                        item_id="r1",
                        pcm=PCM_100MS,
                        sample_rate=24000,
                    ),
                    me.ToolCallEvent(
                        item_id="r1",
                        tool_call=ToolCallBlock(
                            id="c1",
                            name="stream_tool",
                            input='{"q": "x"}',
                        ),
                    ),
                    me.ResponseDoneEvent(item_id="r1"),
                    "WAIT",
                    me.ResponseCreatedEvent(item_id="r2"),
                    me.TranscriptDeltaEvent(
                        item_id="r2",
                        delta="It is sunny.",
                    ),
                    me.AudioDeltaEvent(
                        item_id="r2",
                        pcm=PCM_100MS,
                        sample_rate=24000,
                    ),
                ],
            ],
        )
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            model,
            toolkit=Toolkit(tools=[StreamTool()]),
        )
        audio_deltas = 0
        async with agent:
            transport = FakeTransport(frames=10)
            async with transport:
                async for event in agent.reply_stream(transport):
                    if isinstance(event, DataBlockDeltaEvent):
                        audio_deltas += 1
                        if audio_deltas == 2:
                            await agent.interrupt()

        assistant = [
            message
            for message in agent.state.context
            if message.role == "assistant"
        ][-1]
        self.assertEqual(
            _message_summary(assistant),
            {
                "role": "assistant",
                "id": "r1",
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {
                        "type": "tool_call",
                        "name": "stream_tool",
                        "input": '{"q": "x"}',
                        "state": "finished",
                        "id": "c1",
                    },
                    {
                        "type": "tool_result",
                        "name": "stream_tool",
                        "output": "x-final",
                        "state": "success",
                        "id": "c1",
                    },
                    {"type": "text", "text": "It is sunny."},
                ],
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
                "finished_reason": "interrupted",
            },
        )
        self.assertListEqual(
            [call for call in model.calls if call != "push_audio"],
            [
                "connect(session=1,td_off=False)",
                "tool_result(c1,'x-final')",
                "request_response",
                "cancel",
                "close",
            ],
        )

    async def test_barge_in_after_completed_followup_keeps_reply(
        self,
    ) -> None:
        """Stopping queued audio does not alter a completed reply."""
        model = ScriptedModel(
            [
                [
                    me.ResponseCreatedEvent(item_id="r1"),
                    me.TranscriptDeltaEvent(
                        item_id="r1",
                        delta="Let me check.",
                    ),
                    me.AudioDeltaEvent(
                        item_id="r1",
                        pcm=PCM_100MS,
                        sample_rate=24000,
                    ),
                    me.ToolCallEvent(
                        item_id="r1",
                        tool_call=ToolCallBlock(
                            id="c1",
                            name="stream_tool",
                            input='{"q": "x"}',
                        ),
                    ),
                    me.ResponseDoneEvent(item_id="r1"),
                    "WAIT",
                    me.ResponseCreatedEvent(item_id="r2"),
                    me.TranscriptDeltaEvent(
                        item_id="r2",
                        delta="It is sunny.",
                    ),
                    me.AudioDeltaEvent(
                        item_id="r2",
                        pcm=PCM_100MS,
                        sample_rate=24000,
                    ),
                    me.ResponseDoneEvent(item_id="r2"),
                    me.SpeechStartedEvent(item_id="u2"),
                ],
            ],
        )
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            model,
            toolkit=Toolkit(tools=[StreamTool()]),
        )
        transport = FakeTransport(frames=10)

        async with agent, transport:
            async for _ in agent.reply_stream(transport):
                pass

        self.assertEqual(
            [_message_summary(message) for message in agent.state.context],
            [
                {
                    "role": "assistant",
                    "id": "r1",
                    "content": [
                        {"type": "text", "text": "Let me check."},
                        {
                            "type": "tool_call",
                            "name": "stream_tool",
                            "input": '{"q": "x"}',
                            "state": "finished",
                            "id": "c1",
                        },
                        {
                            "type": "tool_result",
                            "name": "stream_tool",
                            "output": "x-final",
                            "state": "success",
                            "id": "c1",
                        },
                        {"type": "text", "text": "It is sunny."},
                    ],
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    "finished_reason": "completed",
                },
            ],
        )
        self.assertListEqual(
            [call for call in model.calls if call != "push_audio"],
            [
                "connect(session=1,td_off=False)",
                "tool_result(c1,'x-final')",
                "request_response",
                "close",
            ],
        )

    async def test_barge_in_while_tool_runs_clears_first_audio(self) -> None:
        """A reply waiting on a tool still clears its queued audio."""
        tool = BlockingTool()
        model = ScriptedModel(
            [
                [
                    me.ResponseCreatedEvent(item_id="r1"),
                    me.TranscriptDeltaEvent(
                        item_id="r1",
                        delta="Let me check.",
                    ),
                    me.AudioDeltaEvent(
                        item_id="r1",
                        pcm=PCM_100MS,
                        sample_rate=24000,
                    ),
                    me.ToolCallEvent(
                        item_id="r1",
                        tool_call=ToolCallBlock(
                            id="c1",
                            name="stream_tool",
                            input='{"q": "x"}',
                        ),
                    ),
                    me.ResponseDoneEvent(item_id="r1"),
                ],
            ],
        )
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            model,
            toolkit=Toolkit(tools=[tool]),
        )
        transport = FakeTransport(frames=10)

        async with agent, transport:
            async for event in agent.reply_stream(transport):
                if isinstance(event, ToolResultStartEvent):
                    await agent.interrupt()
                    tool.release.set()

        self.assertEqual(transport.cleared, 1)
        self.assertNotIn("truncate", " ".join(model.calls))
        assistant_messages = [
            _message_summary(message)
            for message in agent.state.context
            if message.role == "assistant"
        ]
        self.assertEqual(
            assistant_messages,
            [
                {
                    "role": "assistant",
                    "id": "r1",
                    "content": [
                        {"type": "text", "text": "Let me check."},
                        {
                            "type": "tool_call",
                            "name": "stream_tool",
                            "input": '{"q": "x"}',
                            "state": "finished",
                            "id": "c1",
                        },
                        {
                            "type": "tool_result",
                            "name": "stream_tool",
                            "output": "x-final",
                            "state": "success",
                            "id": "c1",
                        },
                    ],
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    "finished_reason": "interrupted",
                },
            ],
        )


class DropsSocketModel(ScriptedModel):
    """Raises on the first push after the provider closed the socket,
    the way a real WebSocket does before the reader notices."""

    def __init__(self) -> None:
        super().__init__([[], []])
        self.dropped = False

    async def push_audio(self, pcm: bytes) -> None:
        """Fail exactly once, then behave."""
        if self.sessions == 1 and not self.dropped:
            self.dropped = True
            raise ModelDisconnectedError("idle for 180 seconds")
        await super().push_audio(pcm)


class RealtimeAgentDisconnectTest(IsolatedAsyncioTestCase):
    """A send that hits a closed provider socket must not kill the run."""

    async def test_send_on_closed_socket_reconnects_on_next_audio(
        self,
    ) -> None:
        """The failing frame is kept, the run survives, and the next frame
        reconnects and flushes it."""
        model = DropsSocketModel()
        agent = RealtimeAgent("Friday", "be brief", model)
        async with agent:
            transport = FakeTransport(frames=3)
            with self.assertLogs("as", level="INFO") as logs:
                async with transport:
                    async for _ in agent.reply_stream(transport):
                        pass

        self.assertEqual(model.sessions, 2)
        self.assertListEqual(
            [c for c in model.calls if c != "push_audio"],
            [
                "connect(session=1,td_off=False)",
                "connect(session=2,td_off=False)",
                "close",
            ],
        )
        # Three frames captured; the failed one was replayed, so all
        # three reach the provider in the end.
        self.assertEqual(model.calls.count("push_audio"), 3)
        self.assertTrue(
            any("keep talking" in line for line in logs.output),
            logs.output,
        )

    async def test_text_reconnect_delivers_the_kept_audio(self) -> None:
        """A typed turn reconnects, and the frame kept by the failed push
        rides along with that reconnect instead of being stranded."""
        model = DropsSocketModel()
        model.supports_text_input = True
        agent = RealtimeAgent("Friday", "be brief", model)

        async with agent:
            await agent._on_audio(  # pylint: disable=W0212
                AudioFrame(pcm=b"\x00" * 3200),
            )
            await agent.send("hello")

        self.assertListEqual(
            model.calls,
            [
                "connect(session=1,td_off=False)",
                "connect(session=2,td_off=False)",
                "push_audio",
                "push_text('hello')",
                "close",
            ],
        )
