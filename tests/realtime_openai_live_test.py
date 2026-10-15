# -*- coding: utf-8 -*-
"""Tests for GPT-Live protocol and its minimal full-duplex agent path."""
# pylint: disable=protected-access
import asyncio
import base64
import json
import unittest
from typing import AsyncIterator
from unittest.mock import AsyncMock, patch

from agentscope.agent import RealtimeAgent
from agentscope.credential import OpenAICredential
from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    ReplyStartEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import (
    AssistantMsg,
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.realtime import (
    ControlFrame,
    ControlFrameType,
    DelegatedResponseEvent,
    DelegationCreatedEvent,
    OpenAILiveModel,
    PlayoutPosition,
    SessionClosedEvent,
    SessionStartedEvent,
    SessionUsageEvent,
    TimelineAudioDeltaEvent,
    TimelineTranscriptDeltaEvent,
    TransportBase,
    TransportFrame,
)
from agentscope.state import AgentState

CRED = OpenAICredential(api_key="sk-x")


def _model() -> OpenAILiveModel:
    """Build a Live model configured for client delegation."""
    return OpenAILiveModel("gpt-live-1", CRED)


class _FakeWebSocket:
    """A controllable async WebSocket used by lifecycle tests."""

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    def __aiter__(self) -> "_FakeWebSocket":
        return self

    async def __anext__(self) -> str:
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def send(self, raw: str) -> None:
        """Record a command and enqueue its simulated server response."""
        payload = json.loads(raw)
        self.sent.append(payload)
        if payload["type"] == "session.start":
            self.incoming.put_nowait(
                json.dumps(
                    {
                        "type": "session.started",
                        "session": {"id": "live_1"},
                    },
                ),
            )
        elif payload["type"] == "session.close":
            self.incoming.put_nowait(
                json.dumps(
                    {
                        "type": "session.closed",
                        "reason": "close_requested",
                        "session": {"id": "live_1"},
                        "usage": {"seconds": 4.5},
                    },
                ),
            )

    async def close(self) -> None:
        """End the fake server event stream."""
        self.closed = True
        self.incoming.put_nowait(None)


class _FakeTransport(TransportBase):
    """Capture output audio without opening a real sound device."""

    input_sample_rate = 24000
    output_sample_rate = 24000

    def __init__(self) -> None:
        self.audio: list[tuple[bytes, str]] = []
        self.cleared = 0
        self.frames: asyncio.Queue[TransportFrame | None] = asyncio.Queue()

    async def start(self) -> None:
        """Start the in-memory transport."""

    async def close(self) -> None:
        """Close the in-memory transport."""

    async def incoming(self) -> AsyncIterator[TransportFrame]:
        """Yield explicitly queued transport frames."""
        while True:
            frame = await self.frames.get()
            if frame is None:
                return
            yield frame

    async def send_audio(self, pcm: bytes, item_id: str) -> None:
        """Capture one outgoing audio frame."""
        self.audio.append((pcm, item_id))

    async def clear_audio(self) -> PlayoutPosition:
        """Record one playback clear request."""
        self.cleared += 1
        return PlayoutPosition()

    def playout(self) -> PlayoutPosition:
        """Return an empty playback position."""
        return PlayoutPosition()


class _FakeDelegationAgent:
    """Return one deterministic task result without a provider call."""

    def __init__(self) -> None:
        self.state = AgentState()
        self.inputs: list[Msg] = []

    async def reply_stream(
        self,
        inputs: Msg,
        yield_final_msg: bool,
    ) -> AsyncIterator[Msg]:
        """Capture the generated transcript prompt and return a result."""
        self.inputs.append(inputs)
        self.state.context.append(inputs)
        result = AssistantMsg(name="Friday", content="Order 42 shipped.")
        self.state.context.append(result)
        if yield_final_msg:
            yield result


class _ConfirmingDelegationAgent:
    """Pause once for permission, then return a completed result."""

    def __init__(self) -> None:
        self.state = AgentState()
        self.tool_call = ToolCallBlock(
            id="tool_1",
            name="update_order",
            input='{"order_id":"42"}',
        )

    async def reply_stream(
        self,
        inputs: Msg | UserConfirmResultEvent,
        yield_final_msg: bool,
    ) -> AsyncIterator[
        Msg
        | ReplyStartEvent
        | ToolCallStartEvent
        | ToolCallEndEvent
        | ToolResultStartEvent
        | ToolResultTextDeltaEvent
        | ToolResultEndEvent
        | RequireUserConfirmEvent
    ]:
        """Yield a confirmation on the first call and finish on resume."""
        if isinstance(inputs, Msg):
            yield ReplyStartEvent(
                session_id=self.state.session_id,
                reply_id="backend_reply",
                name="Friday",
            )
            yield ToolCallStartEvent(
                reply_id="backend_reply",
                tool_call_id=self.tool_call.id,
                tool_call_name=self.tool_call.name,
            )
            yield ToolCallEndEvent(
                reply_id="backend_reply",
                tool_call_id=self.tool_call.id,
            )
            yield RequireUserConfirmEvent(
                reply_id="backend_reply",
                tool_calls=[self.tool_call],
            )
            return
        yield ToolResultStartEvent(
            reply_id="backend_reply",
            tool_call_id=self.tool_call.id,
            tool_call_name=self.tool_call.name,
        )
        yield ToolResultTextDeltaEvent(
            reply_id="backend_reply",
            tool_call_id=self.tool_call.id,
            delta="Updated",
        )
        yield ToolResultEndEvent(
            reply_id="backend_reply",
            tool_call_id=self.tool_call.id,
            state=ToolResultState.SUCCESS,
        )
        result = AssistantMsg(name="Friday", content="Order 42 updated.")
        if yield_final_msg:
            yield result


class _ExternalDelegationAgent:
    """Pause for an external executor, then finish after its result."""

    def __init__(self) -> None:
        self.state = AgentState()
        self.tool_call = ToolCallBlock(
            id="external_1",
            name="ask_user",
            input='{"question":"Which order?"}',
        )
        self.received: list[
            Msg | ExternalExecutionResultEvent | UserInterruptEvent
        ] = []

    async def reply_stream(
        self,
        inputs: Msg | ExternalExecutionResultEvent | UserInterruptEvent,
        yield_final_msg: bool,
    ) -> AsyncIterator[
        Msg
        | ReplyStartEvent
        | ToolCallStartEvent
        | ToolCallEndEvent
        | RequireExternalExecutionEvent
    ]:
        """Request external execution once and finish on resumption."""
        self.received.append(inputs)
        if isinstance(inputs, Msg):
            yield ReplyStartEvent(
                session_id=self.state.session_id,
                reply_id="external_reply",
                name="Friday",
            )
            yield ToolCallStartEvent(
                reply_id="external_reply",
                tool_call_id=self.tool_call.id,
                tool_call_name=self.tool_call.name,
            )
            yield ToolCallEndEvent(
                reply_id="external_reply",
                tool_call_id=self.tool_call.id,
            )
            yield RequireExternalExecutionEvent(
                reply_id="external_reply",
                tool_calls=[self.tool_call],
            )
            return
        if isinstance(inputs, UserInterruptEvent):
            result = AssistantMsg(name="Friday", content="Interrupted.")
            if yield_final_msg:
                yield result
            return
        result = AssistantMsg(name="Friday", content="Order 42 selected.")
        if yield_final_msg:
            yield result


class _StateDelegationAgent:
    """Record one state mutation before returning a result."""

    def __init__(self, state: AgentState, marker: str) -> None:
        self.state = state
        self.marker = marker

    async def reply_stream(
        self,
        inputs: Msg,
        yield_final_msg: bool,
    ) -> AsyncIterator[Msg]:
        """Append a marker that the next queued task must observe."""
        del inputs
        self.state.tool_context.activated_groups.append(self.marker)
        if yield_final_msg:
            yield AssistantMsg(name="Friday", content=self.marker)


class OpenAILivePayloadTest(unittest.TestCase):
    """Model discovery, startup payloads, and server event parsing."""

    def test_card_and_credential_mapping(self) -> None:
        """The Live card resolves to its own protocol adapter."""
        cards = OpenAILiveModel.list_models()
        classes = {
            item.type: item for item in CRED.get_realtime_model_classes()
        }
        self.assertListEqual(
            [
                (
                    card.name,
                    card.model_type,
                    card.interaction_mode,
                    card.delegation_backend,
                    card.supports_tools,
                    classes[card.model_type].__name__,
                )
                for card in cards
            ],
            [
                (
                    "gpt-live-1",
                    "openai_live",
                    "full_duplex",
                    "session_chat",
                    False,
                    "OpenAILiveModel",
                ),
            ],
        )
        self.assertDictEqual(
            cards[0].parameter_schema,
            {
                "type": "object",
                "properties": {
                    "voice": {
                        "default": "marin",
                        "description": "The voice used for generated audio.",
                        "title": "Voice",
                        "type": "string",
                    },
                    "store": {
                        "default": False,
                        "description": (
                            "Whether to request provider-side storage for "
                            "the Live session."
                        ),
                        "title": "Store Session",
                        "type": "boolean",
                    },
                    "startup_timeout_s": {
                        "default": 15.0,
                        "description": (
                            "Maximum seconds to wait for the Live session "
                            "to start."
                        ),
                        "exclusiveMinimum": 0,
                        "title": "Startup Timeout",
                        "type": "number",
                    },
                    "close_timeout_s": {
                        "default": 15.0,
                        "description": (
                            "Maximum seconds to wait for graceful session "
                            "closure."
                        ),
                        "exclusiveMinimum": 0,
                        "title": "Close Timeout",
                        "type": "number",
                    },
                },
            },
        )

    def test_session_start_payload(self) -> None:
        """Startup delegates backend tasks to the application."""
        model = _model()
        payload = model._session_start("Speak briefly.")
        event_id = payload.pop("event_id")
        self.assertTrue(event_id.startswith("start_"))
        self.assertDictEqual(
            payload,
            {
                "type": "session.start",
                "session": {
                    "model": "gpt-live-1",
                    "instructions": "Speak briefly.",
                    "input": [],
                    "audio": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": 24000,
                        },
                        "output": {"voice": "marin"},
                    },
                    "delegation": {"type": "client"},
                    "store": False,
                },
            },
        )

    def test_server_event_mapping(self) -> None:
        """Live timelines and delegated Responses remain independent."""
        model = _model()
        encoded = base64.b64encode(b"\x01\x00").decode("ascii")
        frames = [
            {
                "type": "session.started",
                "session": {"id": "live_1"},
            },
            {
                "type": "session.input_transcript.delta",
                "delta": "hello",
                "start_ms": 10,
                "end_ms": 20,
            },
            {
                "type": "session.output_transcript.delta",
                "delta": "hi",
                "start_ms": 15,
                "end_ms": 25,
            },
            {"type": "session.output_audio.delta", "delta": encoded},
            {
                "type": "session.delegation.created",
                "offset_ms": 30,
                "delegation": {
                    "id": "delegation_1",
                    "target": "client",
                },
            },
            {
                "type": "response.event",
                "delegation_id": "delegation_1",
                "event": {
                    "type": "response.output_text.delta",
                    "delta": "working",
                },
            },
            {
                "type": "session.usage.updated",
                "usage": {"seconds": 3.25},
                "context_window": {"usage_ratio": 0.2},
            },
            {
                "type": "session.closed",
                "reason": "close_requested",
                "session": {"id": "live_1"},
                "usage": {"seconds": 4.5},
            },
        ]
        self.assertListEqual(
            [model._parse(frame) for frame in frames],
            [
                SessionStartedEvent(session_id="live_1"),
                TimelineTranscriptDeltaEvent(
                    role="user",
                    delta="hello",
                    start_ms=10,
                    end_ms=20,
                ),
                TimelineTranscriptDeltaEvent(
                    role="assistant",
                    delta="hi",
                    start_ms=15,
                    end_ms=25,
                ),
                TimelineAudioDeltaEvent(
                    pcm=b"\x01\x00",
                    sample_rate=24000,
                ),
                DelegationCreatedEvent(
                    delegation_id="delegation_1",
                    target="client",
                    offset_ms=30,
                ),
                DelegatedResponseEvent(
                    delegation_id="delegation_1",
                    event={
                        "type": "response.output_text.delta",
                        "delta": "working",
                    },
                ),
                SessionUsageEvent(
                    seconds=3.25,
                    context_usage_ratio=0.2,
                ),
                SessionClosedEvent(
                    session_id="live_1",
                    reason="close_requested",
                    usage_seconds=4.5,
                ),
            ],
        )
        self.assertEqual(model.usage_seconds, 4.5)

    def test_long_commentary_is_split_at_a_safe_boundary(self) -> None:
        """Long client results are returned as bounded Live updates."""
        content = f"{'a' * 390} boundary {'b' * 20}"
        self.assertListEqual(
            OpenAILiveModel._commentary_chunks(content),
            [f"{'a' * 390} boundary", "b" * 20],
        )


class OpenAILiveLifecycleTest(unittest.IsolatedAsyncioTestCase):
    """Connection startup, audio validation, and graceful close."""

    async def test_connect_audio_and_close(self) -> None:
        """The adapter waits for startup and preserves final usage."""
        ws = _FakeWebSocket()
        connector = AsyncMock(return_value=ws)
        model = _model()
        with patch("websockets.connect", connector):
            await model.connect("Be brief.")
            await model.push_audio(b"\x01\x00")
            await model.send_delegation_result(
                "delegation_1",
                "The order shipped.",
            )
            with self.assertRaises(ValueError):
                await model.push_audio(b"\x01")
            await model.close()

        connector.assert_awaited_once()
        assert connector.await_args is not None
        args, kwargs = connector.await_args
        self.assertEqual(args, ("wss://api.openai.com/v1/live/sessions",))
        self.assertDictEqual(
            kwargs,
            {"additional_headers": {"Authorization": "Bearer sk-x"}},
        )
        self.assertListEqual(
            [item["type"] for item in ws.sent],
            [
                "session.start",
                "session.input_audio.append",
                "session.commentary.append",
                "session.close",
            ],
        )
        self.assertEqual(ws.sent[1]["audio"], "AQA=")
        commentary = ws.sent[2].copy()
        commentary_id = commentary.pop("event_id")
        self.assertTrue(commentary_id.startswith("delegation_result_"))
        self.assertDictEqual(
            commentary,
            {
                "type": "session.commentary.append",
                "delegation_id": "delegation_1",
                "content": "The order shipped.",
            },
        )
        self.assertEqual(model.usage_seconds, 4.5)
        self.assertTrue(ws.closed)


class RealtimeAgentLiveTest(unittest.IsolatedAsyncioTestCase):
    """Minimal projection of overlapping Live transcript and audio."""

    async def test_overlapping_timeline_does_not_barge_in(self) -> None:
        """User transcript growth does not clear assistant playback."""
        model = _model()
        agent = RealtimeAgent("Friday", "Be brief.", model)
        transport = _FakeTransport()
        agent._transport = transport
        self.assertTrue(agent.allows_open_user_reply_checkpoint)

        await agent._on_model_event(SessionStartedEvent(session_id="live_1"))
        await agent._on_model_event(
            TimelineAudioDeltaEvent(
                pcm=b"\x01\x00",
                sample_rate=24000,
            ),
        )
        await agent._on_model_event(
            TimelineTranscriptDeltaEvent(
                role="assistant",
                delta="Sure",
                start_ms=10,
                end_ms=20,
            ),
        )
        await agent._on_model_event(
            TimelineTranscriptDeltaEvent(
                role="user",
                delta="Wait",
                start_ms=15,
                end_ms=25,
            ),
        )

        self.assertListEqual(
            transport.audio,
            [(b"\x01\x00", "live:live_1")],
        )
        self.assertEqual(transport.cleared, 0)
        self.assertListEqual(
            [
                (message.role, message.get_text_content())
                for message in agent.state.context
            ],
            [("assistant", "Sure"), ("user", "Wait")],
        )

    async def test_client_delegation_uses_live_transcript(self) -> None:
        """A client delegation runs the configured session chat agent."""
        model = _model()
        state = AgentState()
        state.context.append(
            UserMsg(
                id="live:user:historical",
                name="user",
                content="Earlier voice context.",
            ),
        )
        backend = _FakeDelegationAgent()

        def _factory(delegation_state: AgentState) -> _FakeDelegationAgent:
            backend.state = delegation_state
            return backend

        model.send_delegation_result = (  # type: ignore[method-assign]
            AsyncMock()
        )
        agent = RealtimeAgent(
            "Friday",
            "Be brief.",
            model,
            state=state,
            delegation_agent_factory=_factory,  # type: ignore[arg-type]
        )

        await agent._on_model_event(
            TimelineTranscriptDeltaEvent(
                role="user",
                delta="Check order 42.",
                start_ms=10,
                end_ms=20,
            ),
        )
        await agent._on_model_event(
            DelegationCreatedEvent(
                delegation_id="delegation_1",
                target="client",
            ),
        )
        tasks = list(agent._tasks)
        await asyncio.gather(*tasks)

        self.assertEqual(len(backend.inputs), 1)
        prompt = backend.inputs[0].get_text_content()
        self.assertIsNotNone(prompt)
        assert prompt is not None
        self.assertIn("User: Check order 42.", prompt)
        backend_ids = {message.id for message in backend.state.context}
        self.assertDictEqual(
            {
                "historical_text": (
                    backend.state.context[0].get_text_content()
                ),
                "current_live_ids": backend_ids & agent._live_message_ids,
                "backend_texts": [
                    message.get_text_content()
                    for message in backend.state.context
                ],
            },
            {
                "historical_text": "Earlier voice context.",
                "current_live_ids": set(),
                "backend_texts": [
                    "Earlier voice context.",
                    prompt,
                    "Order 42 shipped.",
                ],
            },
        )
        model.send_delegation_result.assert_awaited_once_with(
            "delegation_1",
            "Order 42 shipped.",
        )
        self.assertDictEqual(agent._delegations, {})

    async def test_client_delegation_resumes_after_confirmation(self) -> None:
        """Realtime control routes approval back to the delegated agent."""
        model = _model()
        backend = _ConfirmingDelegationAgent()
        model.send_delegation_result = (  # type: ignore[method-assign]
            AsyncMock()
        )
        agent = RealtimeAgent(
            "Friday",
            "Be brief.",
            model,
            delegation_agent_factory=lambda _state: backend,
        )

        await agent._on_model_event(
            DelegationCreatedEvent(
                delegation_id="delegation_1",
                target="client",
            ),
        )
        await asyncio.gather(*list(agent._tasks))
        self.assertDictEqual(
            agent._delegated_tool_calls,
            {"tool_1": "delegation_1"},
        )
        delegation = agent._delegations["delegation_1"]
        self.assertListEqual(delegation.buffered_events, [])

        sync_calls = 0
        original_sync = agent._sync_delegation_state

        def _count_sync(current: object) -> None:
            nonlocal sync_calls
            sync_calls += 1
            original_sync(current)  # type: ignore[arg-type]

        agent._sync_delegation_state = (  # type: ignore[method-assign]
            _count_sync
        )

        await agent.send(
            UserConfirmResultEvent(
                reply_id="backend_reply",
                confirm_results=[
                    ConfirmResult(
                        confirmed=True,
                        tool_call=backend.tool_call,
                    ),
                ],
            ),
        )
        await asyncio.gather(*list(agent._tasks))

        model.send_delegation_result.assert_awaited_once_with(
            "delegation_1",
            "Order 42 updated.",
        )
        self.assertDictEqual(agent._delegated_tool_calls, {})
        self.assertEqual(sync_calls, 2)

    async def test_client_delegations_use_latest_serial_state(self) -> None:
        """Queued tasks are created only after the active task syncs state."""
        model = _model()
        model.send_delegation_result = (  # type: ignore[method-assign]
            AsyncMock()
        )
        state = AgentState()
        factory_snapshots: list[list[str]] = []

        def _factory(snapshot: AgentState) -> _StateDelegationAgent:
            factory_snapshots.append(
                list(snapshot.tool_context.activated_groups),
            )
            marker = f"task-{len(factory_snapshots)}"
            return _StateDelegationAgent(snapshot, marker)

        agent = RealtimeAgent(
            "Friday",
            "Be brief.",
            model,
            state=state,
            delegation_agent_factory=_factory,  # type: ignore[arg-type]
        )

        await agent._on_model_event(
            DelegationCreatedEvent(
                delegation_id="delegation_1",
                target="client",
            ),
        )
        await agent._on_model_event(
            DelegationCreatedEvent(
                delegation_id="delegation_2",
                target="client",
            ),
        )
        while agent._tasks:
            await asyncio.gather(*list(agent._tasks))

        self.assertDictEqual(
            {
                "factory_snapshots": factory_snapshots,
                "shared_groups": state.tool_context.activated_groups,
                "delegations": agent._delegations,
                "queue": list(agent._delegation_queue),
                "active": agent._active_delegation_id,
            },
            {
                "factory_snapshots": [[], ["task-1"]],
                "shared_groups": ["task-1", "task-2"],
                "delegations": {},
                "queue": [],
                "active": None,
            },
        )

    async def test_client_delegation_resumes_external_execution(self) -> None:
        """External results route back to the parked delegated agent."""
        model = _model()
        backend = _ExternalDelegationAgent()
        model.send_delegation_result = (  # type: ignore[method-assign]
            AsyncMock()
        )
        agent = RealtimeAgent(
            "Friday",
            "Be brief.",
            model,
            delegation_agent_factory=lambda _state: backend,
        )

        await agent._on_model_event(
            DelegationCreatedEvent(
                delegation_id="delegation_1",
                target="client",
            ),
        )
        await asyncio.gather(*list(agent._tasks))
        model.send_delegation_result.assert_not_awaited()
        self.assertDictEqual(
            agent._delegated_tool_calls,
            {"external_1": "delegation_1"},
        )
        queued = []
        while not agent._out.empty():
            item = agent._out.get_nowait()
            queued.append(
                (type(item.event).__name__, item.checkpoint is not None),
            )
        self.assertIn(
            ("RequireExternalExecutionEvent", True),
            queued,
        )

        external_result = ExternalExecutionResultEvent(
            reply_id="external_reply",
            execution_results=[
                ToolResultBlock(
                    id="external_1",
                    name="ask_user",
                    output=[TextBlock(text="Order 42")],
                    state=ToolResultState.SUCCESS,
                ),
            ],
        )
        await agent._on_control(
            ControlFrame(
                type=ControlFrameType.EXTERNAL_EXECUTION_RESULT,
                data=external_result.model_dump(mode="json"),
            ),
        )
        await asyncio.gather(*list(agent._tasks))

        self.assertEqual(backend.received[-1], external_result)
        model.send_delegation_result.assert_awaited_once_with(
            "delegation_1",
            "Order 42 selected.",
        )
        self.assertDictEqual(agent._delegated_tool_calls, {})

    async def test_parked_delegation_intentionally_holds_fifo(self) -> None:
        """A parked task holds FIFO until its input deadline expires."""
        model = _model()
        first = _ExternalDelegationAgent()
        second = _FakeDelegationAgent()
        created: list[object] = []

        def _factory(state: AgentState) -> object:
            del state
            backend = first if not created else second
            created.append(backend)
            return backend

        model.send_delegation_result = (  # type: ignore[method-assign]
            AsyncMock()
        )
        agent = RealtimeAgent(
            "Friday",
            "Be brief.",
            model,
            delegation_agent_factory=_factory,  # type: ignore[arg-type]
        )

        await agent._on_model_event(
            DelegationCreatedEvent(
                delegation_id="delegation_1",
                target="client",
            ),
        )
        await asyncio.gather(*list(agent._tasks))
        await agent._on_model_event(
            DelegationCreatedEvent(
                delegation_id="delegation_2",
                target="client",
            ),
        )

        self.assertDictEqual(
            {
                "created": created,
                "active": agent._active_delegation_id,
                "queue": list(agent._delegation_queue),
            },
            {
                "created": [first],
                "active": "delegation_1",
                "queue": ["delegation_2"],
            },
        )

        delegation = agent._delegations["delegation_1"]
        self.assertIsNotNone(delegation.timeout_task)
        agent._cancel_delegation_timeout(delegation)
        with patch(
            "agentscope.agent._realtime._agent."
            "_DELEGATION_INPUT_TIMEOUT_SECS",
            0,
        ):
            await agent._expire_client_delegation(delegation)
        while agent._tasks:
            await asyncio.gather(*list(agent._tasks))

        self.assertDictEqual(
            {
                "first_received": [
                    type(item).__name__ for item in first.received
                ],
                "created": created,
                "active": agent._active_delegation_id,
                "queue": list(agent._delegation_queue),
                "delegations": agent._delegations,
                "expired": delegation.expiring,
            },
            {
                "first_received": ["Msg", "UserInterruptEvent"],
                "created": [first, second],
                "active": None,
                "queue": [],
                "delegations": {},
                "expired": True,
            },
        )
        self.assertListEqual(
            model.send_delegation_result.await_args_list,
            [
                unittest.mock.call(
                    "delegation_1",
                    "The delegated task was cancelled because the required "
                    "user input or external execution was not provided in "
                    "time.",
                ),
                unittest.mock.call(
                    "delegation_2",
                    "Order 42 shipped.",
                ),
            ],
        )

    async def test_unmatched_external_result_is_logged(self) -> None:
        """An external result without a delegation is not silent."""
        agent = RealtimeAgent("Friday", "Be brief.", _model())
        event = ExternalExecutionResultEvent(
            reply_id="external_reply",
            execution_results=[
                ToolResultBlock(
                    id="unknown_tool",
                    name="ask_user",
                    output=[TextBlock(text="Order 42")],
                    state=ToolResultState.SUCCESS,
                ),
            ],
        )

        with self.assertLogs("as", level="WARNING") as captured:
            await agent.send(event)

        self.assertListEqual(
            captured.output,
            [
                "WARNING:as:RealtimeAgent: ignored external execution "
                "results without an active delegation: ['unknown_tool']",
            ],
        )

    async def test_live_transcript_samples_exact_snapshots(
        self,
    ) -> None:
        """Only sampled deltas carry their exact persistence boundary."""
        model = _model()
        agent = RealtimeAgent("Friday", "Be brief.", model)
        events = [
            TimelineTranscriptDeltaEvent(
                role="user",
                delta=delta,
                start_ms=start_ms,
                end_ms=end_ms,
            )
            for delta, start_ms, end_ms in [
                ("one", 0, 100),
                ("two", 100, 200),
                ("three", 2_000, 2_100),
                ("four", 2_100, 2_200),
            ]
        ]

        with patch(
            "agentscope.agent._realtime._agent.time.monotonic",
            return_value=1.0,
        ):
            for event in events:
                await agent._on_model_event(event)

        checkpoints = []
        while not agent._out.empty():
            queued = agent._out.get_nowait()
            if isinstance(queued.event, TextBlockDeltaEvent):
                checkpoints.append(
                    {
                        "delta": queued.event.delta,
                        "checkpoint": (
                            None
                            if queued.checkpoint is None
                            else queued.checkpoint.context[
                                0
                            ].get_text_content()
                        ),
                    },
                )

        self.assertListEqual(
            checkpoints,
            [
                {"delta": "one", "checkpoint": "one"},
                {"delta": "two", "checkpoint": None},
                {"delta": "three", "checkpoint": "onetwothree"},
                {"delta": "four", "checkpoint": None},
            ],
        )

    async def test_live_transcript_keeps_a_timestamped_bounded_window(
        self,
    ) -> None:
        """Prompt history stays bounded while retaining timeline metadata."""
        model = _model()
        agent = RealtimeAgent("Friday", "Be brief.", model)
        events = [
            TimelineTranscriptDeltaEvent(
                role="user",
                delta="12345",
                start_ms=10,
                end_ms=20,
            ),
            TimelineTranscriptDeltaEvent(
                role="assistant",
                delta="abcde",
                start_ms=21,
                end_ms=30,
            ),
            TimelineTranscriptDeltaEvent(
                role="assistant",
                delta="fg",
                start_ms=31,
                end_ms=35,
            ),
        ]

        with patch(
            "agentscope.agent._realtime._agent._HISTORY_MAX_TEXT_CHARS",
            8,
        ):
            for event in events:
                await agent._on_model_event(event)

        self.assertDictEqual(
            {
                "transcript": list(agent._live_transcript),
                "chars": agent._live_transcript_chars,
            },
            {
                "transcript": [
                    ("assistant", "abcde", 21, 30),
                    ("assistant", "fg", 31, 35),
                ],
                "chars": 7,
            },
        )


if __name__ == "__main__":
    unittest.main()
