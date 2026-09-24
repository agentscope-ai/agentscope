# -*- coding: utf-8 -*-
"""Unit tests for RealtimeAgent, driven by a scripted model and a fake
transport — no network, no sound card."""
# pylint: disable=protected-access, unused-argument
import asyncio
from typing import Any, AsyncIterator, Sequence
from unittest.async_case import IsolatedAsyncioTestCase
from utils import AnyString

from agentscope.agent import RealtimeAgent, TurnAggregator
from agentscope.credential import DashScopeCredential
from agentscope.event import (
    ReplyEndEvent,
    ReplyStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
)
from agentscope.message import AssistantMsg, Msg, UserMsg
from agentscope.realtime import (
    AudioFrame,
    ModelDisconnectedError,
    PlayoutPosition,
    RealtimeModelBase,
    RealtimeModelCard,
    SpeechTransition,
    TransportBase,
    TruncationSupport,
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

REPLY_R1 = [
    me.SpeechEndedEvent(item_id="u1"),
    me.InputTranscriptionEvent(item_id="u1", text="讲个故事"),
    me.ResponseCreatedEvent(item_id="r1"),
]
for _word in ["从前", "有座山", "山里", "有座庙", "庙里", "有个", "老和尚"]:
    REPLY_R1 += [
        me.TranscriptDeltaEvent(item_id="r1", delta=_word),
        me.AudioDeltaEvent(item_id="r1", pcm=PCM_100MS, sample_rate=24000),
    ]

REPLY_R2 = [
    me.InputTranscriptionEvent(item_id="u3", text="你好"),
    me.ResponseCreatedEvent(item_id="r2"),
    me.TranscriptDeltaEvent(item_id="r2", delta="你好呀"),
    me.AudioDeltaEvent(item_id="r2", pcm=PCM_100MS, sample_rate=24000),
    me.ResponseDoneEvent(item_id="r2", input_tokens=10, output_tokens=3),
]


class ScriptedModel(RealtimeModelBase):
    """Plays one event script per session and records every call."""

    truncation = TruncationSupport.NONE
    type = "scripted"

    def __init__(self, scripts: list[list[Any]]) -> None:
        card = RealtimeModelCard(
            name="scripted",
            label="scripted",
            input_sample_rate=16000,
            output_sample_rate=24000,
        )
        super().__init__(
            "scripted",
            DashScopeCredential(api_key="sk-x"),
            model_card=card,
        )
        self.scripts = scripts
        self.calls: list[str] = []
        self.sessions = 0
        self.instructions = ""
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

    async def truncate(
        self,
        item_id: str,
        played_ms: int,
        played_text: str,
    ) -> None:
        """Record what the agent thinks the user heard."""
        self.calls.append(f"truncate({item_id},{played_ms}ms,{played_text!r})")


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
    """Emits ``frames`` chunks of silence, reports 320 ms played."""

    input_sample_rate = 16000
    output_sample_rate = 24000

    def __init__(
        self,
        frames: int,
        played_ms: int = 320,
        reported_item: str | None = None,
    ) -> None:
        self.frames = frames
        self.played_ms = played_ms
        self.reported_item = reported_item
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
        """Report the configured position in the current item."""
        return PlayoutPosition(
            item_id=(
                self.reported_item
                if self.reported_item is not None
                else self.item
            ),
            played_ms=self.played_ms,
            first_played_at=1.0,
        )


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

    async def test_barge_in_truncates_to_what_was_heard(self) -> None:
        """A barge-in mid-reply cuts both contexts to the played prefix
        and the duplicate speech_started is swallowed by the lock."""
        script = REPLY_R1 + [
            me.SpeechStartedEvent(item_id="u2"),
            me.SpeechStartedEvent(item_id="u2"),
        ]
        model = ScriptedModel([script])
        agent = RealtimeAgent("Friday", "be brief", model)
        transport = FakeTransport(frames=3)

        # Rebuild the agent's message from its events the way a client
        # does; the text deltas ran ahead of what was heard.
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
                "讲个故事",
                ("reply_end", "completed"),  # the user's turn
                "从前",
                "有座山",
                "山里",
                "有座庙",
                "庙里",
                "有个",
                "老和尚",
                ("user_start", "u2"),
                ("reply_end", "interrupted"),
            ],
        )
        self.assertEqual(rebuilt.get_text_content(), "从前有座山山里有座庙")
        self.assertEqual(transport.cleared, 1)
        # Audio frames interleave with model events on the transport's
        # clock, so they are counted rather than positioned.
        self.assertListEqual(
            [c for c in model.calls if c != "push_audio"],
            [
                "connect(session=1,td_off=False)",
                "truncate(r1,320ms,'从前有座山山里有座庙')",
                "cancel",
                "close",
            ],
        )
        self.assertEqual(model.calls.count("push_audio"), 3)
        self.assertListEqual(
            [(m.role, m.get_text_content()) for m in agent.state.context],
            [("user", "讲个故事"), ("assistant", "从前有座山山里有座庙")],
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
        correction_events = []

        async with agent, transport:
            async for event in agent.reply_stream(transport):
                if getattr(event, "reply_id", None) == "r1":
                    rebuilt.append_event(event)
                if (
                    isinstance(event, TextBlockEndEvent)
                    and event.text is not None
                ):
                    correction_events.append(
                        event.model_dump(exclude={"id", "created_at"}),
                    )

        self.assertEqual(
            correction_events,
            [
                {
                    "type": "TEXT_BLOCK_END",
                    "metadata": {},
                    "reply_id": "r1",
                    "block_id": AnyString(),
                    "text": "从前有座山山里有座庙",
                },
            ],
        )
        self.assertEqual(rebuilt.get_text_content(), "从前有座山山里有座庙")
        self.assertEqual(transport.cleared, 1)
        self.assertListEqual(
            [call for call in model.calls if call != "push_audio"],
            [
                "connect(session=1,td_off=False)",
                "truncate(r1,320ms,'从前有座山山里有座庙')",
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
                ("user", "讲个故事", None),
                (
                    "assistant",
                    "从前有座山山里有座庙",
                    ReplyFinishedReason.COMPLETED,
                ),
            ],
        )

    async def test_barge_in_after_completed_playout_keeps_reply(self) -> None:
        """Speaking after all queued audio played does not truncate it."""
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
        transport = FakeTransport(frames=3, played_ms=700)
        correction_events = []

        async with agent, transport:
            async for event in agent.reply_stream(transport):
                if (
                    isinstance(event, TextBlockEndEvent)
                    and event.text is not None
                ):
                    correction_events.append(event)

        self.assertListEqual(correction_events, [])
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
                ("user", "讲个故事", None),
                (
                    "assistant",
                    "从前有座山山里有座庙庙里有个老和尚",
                    ReplyFinishedReason.COMPLETED,
                ),
            ],
        )

    async def test_zero_playout_keeps_no_transcript_prefix(self) -> None:
        """A response interrupted before playback retains no first word."""
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
        transport = FakeTransport(frames=3, played_ms=0)
        corrections = []

        async with agent, transport:
            async for event in agent.reply_stream(transport):
                if (
                    isinstance(event, TextBlockEndEvent)
                    and event.text is not None
                ):
                    corrections.append(
                        event.model_dump(exclude={"id", "created_at"}),
                    )

        self.assertEqual(
            corrections,
            [
                {
                    "type": "TEXT_BLOCK_END",
                    "metadata": {},
                    "reply_id": "r1",
                    "block_id": AnyString(),
                    "text": "",
                },
            ],
        )
        self.assertListEqual(
            [call for call in model.calls if call != "push_audio"],
            [
                "connect(session=1,td_off=False)",
                "truncate(r1,0ms,'')",
                "close",
            ],
        )
        self.assertListEqual(
            [
                (message.role, message.get_text_content())
                for message in agent.state.context
            ],
            [("user", "讲个故事")],
        )

    async def test_mismatched_playout_still_cancels_active_reply(self) -> None:
        """A stale previous item cannot make the current reply
        uninterruptible."""
        script = REPLY_R1 + [me.SpeechStartedEvent(item_id="u2")]
        model = ScriptedModel([script])
        agent = RealtimeAgent("Friday", "be brief", model)
        transport = FakeTransport(
            frames=3,
            played_ms=700,
            reported_item="previous-item",
        )

        async with agent:
            summary = await self._collect(agent, transport)

        self.assertListEqual(
            summary,
            [
                ("user", "讲个故事"),
                ("reply_end", "interrupted"),
            ],
        )
        self.assertEqual(transport.cleared, 1)
        self.assertListEqual(
            [call for call in model.calls if call != "push_audio"],
            [
                "connect(session=1,td_off=False)",
                "truncate(r1,0ms,'')",
                "cancel",
                "close",
            ],
        )
        self.assertListEqual(
            [
                (message.role, message.get_text_content())
                for message in agent.state.context
            ],
            [("user", "讲个故事")],
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
        # The orphaned reply was dropped, so one message carried over,
        # riding along in the instructions of the new session.
        self.assertIn("connect(session=2,td_off=False)", model.calls)
        self.assertEqual(
            model.instructions,
            "be brief\n\n## Conversation so far\nuser: 讲个故事",
        )
        # Events produced while no run was active are delivered first;
        # a reply streamed with nobody listening is cut off exactly once.
        self.assertListEqual(
            summary,
            [
                ("user", "讲个故事"),
                ("reply_end", "interrupted"),
                ("user", "你好"),
                ("reply_end", "completed"),
            ],
        )
        self.assertListEqual(
            [(m.role, m.get_text_content()) for m in agent.state.context],
            [("user", "讲个故事"), ("user", "你好"), ("assistant", "你好呀")],
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

        expected_replay = [
            {
                "name": "summary",
                "content": [
                    {
                        "type": "text",
                        "text": "Earlier summary",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "role": "system",
                "id": AnyString(),
                "metadata": {},
                "created_at": AnyString(),
                "usage": None,
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
            },
            {
                "name": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "hello",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "role": "user",
                "id": AnyString(),
                "metadata": {},
                "created_at": AnyString(),
                "usage": None,
                "finished_at": AnyString(),
                "finished_reason": None,
                "structured_output": None,
                "error": None,
            },
            {
                "name": "Friday",
                "content": [
                    {
                        "type": "text",
                        "text": "hi",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "role": "assistant",
                "id": AnyString(),
                "metadata": {},
                "created_at": AnyString(),
                "usage": None,
                "finished_at": None,
                "finished_reason": "completed",
                "structured_output": None,
                "error": None,
            },
            {
                "name": "Friday",
                "content": [
                    {
                        "type": "text",
                        "text": "legacy complete",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "role": "assistant",
                "id": AnyString(),
                "metadata": {},
                "created_at": AnyString(),
                "usage": None,
                "finished_at": "2026-01-01T00:00:00",
                "finished_reason": None,
                "structured_output": None,
                "error": None,
            },
        ]
        self.assertDictEqual(
            {
                "disconnected_before_reconnect": (
                    disconnected_before_reconnect
                ),
                "instructions": model.instructions,
                "sessions": model.sessions,
                "calls": model.calls,
                "replayed": [
                    [message.model_dump(mode="json") for message in replay]
                    for replay in model.replayed
                ],
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

    async def test_old_downlink_cannot_disconnect_new_session(self) -> None:
        """Late completion of an old iterator leaves reconnection active."""
        model = QueueSessionModel()
        agent = RealtimeAgent("Friday", "be brief", model)

        async with agent:
            await model.iterator_started[0].wait()
            old_queue = model.event_queues[0]
            agent._mark_disconnected()  # pylint: disable=protected-access
            await agent.connect()
            old_queue.put_nowait(None)
            await model.iterator_finished[0].wait()
            await model.iterator_started[1].wait()

            active_state = {
                "connected": agent._connected,  # pylint: disable=W0212
                "connection_generation": agent._connection_generation,
                "sessions": model.sessions,
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
            [("user", "讲个故事"), ("reply_end", "interrupted")],
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
            [[me.InputTranscriptionEvent(item_id="u1", text="嗯。")]],
        )
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            model,
            aggregator=TurnAggregator(backchannels=frozenset({"嗯"})),
        )
        async with agent:
            summary = await self._collect(agent, FakeTransport(frames=1))

        self.assertListEqual(summary, [])
        self.assertListEqual(agent.state.context, [])


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
    ) -> tuple[list[tuple[str, Any]], ScriptedModel]:
        """Run one tool-calling reply; answer a permission prompt with
        *confirm* if one appears. Returns the tool-related events."""
        model = ScriptedModel([_tool_script(tool.name)])
        agent = RealtimeAgent(
            "Friday",
            "be brief",
            model,
            toolkit=Toolkit(tools=[tool]),
        )
        events: list[tuple[str, Any]] = []
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
                                ("ask", [c.name for c in event.tool_calls]),
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
                            events.append(("result_end", event.state))
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
                ("result_end", "success"),
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
                ("ask", ["ask_tool"]),
                ("result_start", "ask_tool"),
                ("delta", "x-a"),
                ("delta", "x-b"),
                ("result_end", "success"),
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
                ("ask", ["ask_tool"]),
                ("result_start", "ask_tool"),
                ("delta", 'Tool "ask_tool" denied by user.'),
                ("result_end", "denied"),
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
                ("result_end", "error"),
            ],
        )
        self.assertIn("tool_result(c1,'boom')", model.calls)


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
                    me.InputTranscriptionEvent(item_id="u1", text="查天气"),
                    me.ResponseCreatedEvent(item_id="r1"),
                    me.TranscriptDeltaEvent(item_id="r1", delta="我查一下"),
                    me.AudioDeltaEvent(
                        item_id="r1",
                        pcm=b"\x01\x00",
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
                    me.ResponseDoneEvent(
                        item_id="r1",
                        input_tokens=5,
                        output_tokens=2,
                    ),
                    "WAIT",
                    me.ResponseCreatedEvent(item_id="r2"),
                    me.TranscriptDeltaEvent(item_id="r2", delta="今天晴"),
                    me.AudioDeltaEvent(
                        item_id="r2",
                        pcm=b"\x01\x00",
                        sample_rate=24000,
                    ),
                    me.ResponseDoneEvent(
                        item_id="r2",
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
                    events.append(event.model_dump(mode="json"))

        self.assertListEqual(
            events,
            [
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "REPLY_START",
                    "session_id": AnyString(),
                    "reply_id": "u1",
                    "name": "user",
                    "role": "user",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_START",
                    "reply_id": "u1",
                    "block_id": AnyString(),
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_DELTA",
                    "reply_id": "u1",
                    "block_id": AnyString(),
                    "delta": "查天气",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_END",
                    "reply_id": "u1",
                    "block_id": AnyString(),
                    "text": None,
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "REPLY_END",
                    "session_id": AnyString(),
                    "reply_id": "u1",
                    "finished_reason": "completed",
                    "error": None,
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "REPLY_START",
                    "session_id": AnyString(),
                    "reply_id": "r1",
                    "name": "Friday",
                    "role": "assistant",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "MODEL_CALL_START",
                    "reply_id": "r1",
                    "model_name": "scripted",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_START",
                    "reply_id": "r1",
                    "block_id": AnyString(),
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_DELTA",
                    "reply_id": "r1",
                    "block_id": AnyString(),
                    "delta": "我查一下",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "DATA_BLOCK_START",
                    "reply_id": "r1",
                    "block_id": AnyString(),
                    "media_type": "audio/pcm;rate=24000",
                    "name": None,
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "DATA_BLOCK_DELTA",
                    "reply_id": "r1",
                    "block_id": AnyString(),
                    "media_type": "audio/pcm;rate=24000",
                    "data": "AQA=",
                    "url": None,
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_END",
                    "reply_id": "r1",
                    "block_id": AnyString(),
                    "text": None,
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "DATA_BLOCK_END",
                    "reply_id": "r1",
                    "block_id": AnyString(),
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "MODEL_CALL_END",
                    "reply_id": "r1",
                    "input_tokens": 5,
                    "output_tokens": 2,
                    "cache_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "finished_reason": "completed",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TOOL_CALL_START",
                    "reply_id": "r1",
                    "tool_call_id": "c1",
                    "tool_call_name": "stream_tool",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TOOL_CALL_END",
                    "reply_id": "r1",
                    "tool_call_id": "c1",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TOOL_RESULT_START",
                    "reply_id": "r1",
                    "tool_call_id": "c1",
                    "tool_call_name": "stream_tool",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TOOL_RESULT_TEXT_DELTA",
                    "reply_id": "r1",
                    "tool_call_id": "c1",
                    "delta": "x-a",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TOOL_RESULT_TEXT_DELTA",
                    "reply_id": "r1",
                    "tool_call_id": "c1",
                    "delta": "x-b",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TOOL_RESULT_END",
                    "reply_id": "r1",
                    "tool_call_id": "c1",
                    "state": "success",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "MODEL_CALL_START",
                    "reply_id": "r1",
                    "model_name": "scripted",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_START",
                    "reply_id": "r1",
                    "block_id": AnyString(),
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_DELTA",
                    "reply_id": "r1",
                    "block_id": AnyString(),
                    "delta": "今天晴",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "DATA_BLOCK_START",
                    "reply_id": "r1",
                    "block_id": AnyString(),
                    "media_type": "audio/pcm;rate=24000",
                    "name": None,
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "DATA_BLOCK_DELTA",
                    "reply_id": "r1",
                    "block_id": AnyString(),
                    "media_type": "audio/pcm;rate=24000",
                    "data": "AQA=",
                    "url": None,
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_END",
                    "reply_id": "r1",
                    "block_id": AnyString(),
                    "text": None,
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "DATA_BLOCK_END",
                    "reply_id": "r1",
                    "block_id": AnyString(),
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "MODEL_CALL_END",
                    "reply_id": "r1",
                    "input_tokens": 9,
                    "output_tokens": 3,
                    "cache_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "finished_reason": "completed",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "REPLY_END",
                    "session_id": AnyString(),
                    "reply_id": "r1",
                    "finished_reason": "completed",
                    "error": None,
                },
            ],
        )
        # The context records the whole turn as one assistant message: the
        # first words, the tool call and its result, then the spoken answer.
        self.assertListEqual(
            [m.model_dump() for m in agent.state.context],
            [
                {
                    "name": "user",
                    "role": "user",
                    "id": "u1",
                    "content": [
                        {
                            "type": "text",
                            "text": "查天气",
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "metadata": {},
                    "created_at": AnyString(),
                    "usage": None,
                    "finished_at": AnyString(),
                    "finished_reason": None,
                    "structured_output": None,
                    "error": None,
                },
                {
                    "name": "Friday",
                    "role": "assistant",
                    "id": "r1",
                    "content": [
                        {
                            "type": "text",
                            "text": "我查一下",
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                        {
                            "type": "tool_call",
                            "id": "c1",
                            "name": "stream_tool",
                            "input": '{"q": "x"}',
                            "state": "pending",
                            "suggested_rules": [],
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                        {
                            "type": "tool_result",
                            "id": "c1",
                            "name": "stream_tool",
                            "output": "x-final",
                            "state": "success",
                            "metadata": {},
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                        {
                            "type": "text",
                            "text": "今天晴",
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "metadata": {},
                    "created_at": AnyString(),
                    # Both model calls of the reply, summed.
                    "usage": {
                        "input_tokens": 14,
                        "output_tokens": 5,
                        "cache_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                    "finished_at": AnyString(),
                    "finished_reason": "completed",
                    "structured_output": None,
                    "error": None,
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
