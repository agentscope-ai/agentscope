# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression coverage for delayed interrupt delivery to ChatService.

Uses the real interrupt producer, queued events, Agent, and in-memory bus.
Only external resource factories and durable storage are substituted,
matching the existing service_chat_* test fixtures.
"""
import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, AsyncGenerator, AsyncIterator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from utils import MockModel

from agentscope.agent import Agent
from agentscope.app import _bus_ops
from agentscope.app._service import ChatService
from agentscope.app._manager import ChatRunRegistry, WakeupDispatcher
from agentscope.app._manager import _wakeup_dispatcher as wakeup_module
from agentscope.app.channel import ChannelClients, ChannelEvent
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChannelOrigin,
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
)
from agentscope.event import (
    AgentEvent,
    ConfirmResult,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import AssistantMsg, Msg, TextBlock, ToolCallBlock
from agentscope.model import ChatResponse
from agentscope.permission import AdditionalWorkingDirectory
from agentscope.state import AgentState
from agentscope.tool import Toolkit
from agentscope.types import ReplyFinishedReason

_WORKDIR = "/tmp/agentscope-stale-interrupt-test"


class _Storage:
    """Return detached records and preserve every persisted reply."""

    def __init__(self, session: SessionRecord, message: Msg) -> None:
        """Keep one session and its current reply."""
        self.session = session
        self.message = message
        self.get_message_calls = 0
        self.written_messages: list[Msg] = []

    async def get_session(self, *_: object) -> SessionRecord:
        """Return a detached state snapshot, as a persistent backend does."""
        return self.session.model_copy(deep=True)

    async def get_message(self, *_: object) -> Msg:
        """Count reads of the reply the service is about to modify."""
        self.get_message_calls += 1
        return self.message.model_copy(deep=True)

    async def update_session_state(
        self,
        *,
        state: AgentState,
        **_: object,
    ) -> None:
        """Persist a detached final state for the assertions."""
        self.session.state = state.model_copy(deep=True)

    async def upsert_message(
        self,
        _user: str,
        _session: str,
        message: Msg,
    ) -> None:
        """Save a detached reply and count every message write."""
        self.message = message.model_copy(deep=True)
        self.written_messages.append(self.message)


class _RecordingBus(InMemoryMessageBus):
    """Keep event evidence even when normal checkpoint trimming runs."""

    def __init__(self) -> None:
        """Capture published events independently of replay-log trimming."""
        super().__init__()
        self.events: list[dict[str, Any]] = []

    async def log_append(
        self,
        key: str,
        payload: dict,
        **kwargs: Any,
    ) -> str:
        """Record actual service events before normal in-memory storage."""
        self.events.append(dict(payload))
        return await super().log_append(key, payload, **kwargs)


class _NoCallModel(MockModel):
    """An interrupt must not need a provider request."""

    def __init__(self) -> None:
        """Initialize the offline model and its forbidden-call counter."""
        super().__init__(stream=False)
        self.calls = 0

    async def _call_api(self, *_: Any, **__: Any) -> ChatResponse:
        """Fail if interrupt handling enters the reasoning loop."""
        self.calls += 1
        raise AssertionError("Interrupt unexpectedly invoked the model")


@dataclass
class _ServiceFixture:
    """The session resources shared by interrupt and channel regressions."""

    service: ChatService
    storage: _Storage
    bus: _RecordingBus
    invoked: list[object]


class TestStaleInterruptService(IsolatedAsyncioTestCase):
    """A delayed trigger must not end whichever reply is now parked."""

    @staticmethod
    def _fixture(
        tool_state: str | None,
    ) -> _ServiceFixture:
        """Build a detached session and observe the actual Agent stream."""
        user_id, session_id, agent_id = "user-1", "session-1", "agent-1"
        message = AssistantMsg(
            id="current-reply",
            name="agent",
            content=(
                [
                    ToolCallBlock(
                        id="current-tool",
                        name="pending-tool",
                        input="{}",
                        state=tool_state,
                    ),
                ]
                if tool_state is not None
                else [TextBlock(text="Previous answer.")]
            ),
            finished_reason=(
                ReplyFinishedReason.COMPLETED if tool_state is None else None
            ),
        )
        state = AgentState(context=[message.model_copy(deep=True)])
        state.session_id = session_id
        state.reply_id = message.id
        state.permission_context.working_directories[
            _WORKDIR
        ] = AdditionalWorkingDirectory(path=_WORKDIR, source="session")
        session = SessionRecord(
            id=session_id,
            user_id=user_id,
            agent_id=agent_id,
            state=state,
            config=SessionConfig(
                workspace_id="workspace-1",
                chat_model_config=ChatModelConfig(
                    type="test",
                    credential_id="credential-1",
                    model="offline-model",
                    parameters={},
                ),
            ),
        )
        record = AgentRecord(
            id=agent_id,
            user_id=user_id,
            data=AgentData(name="agent"),
        )
        storage, bus = _Storage(session, message), _RecordingBus()
        invoked: list[object] = []

        class _ObservedAgent(Agent):
            """Observe delegation without replacing Agent behavior."""

            async def reply_stream(
                self,
                inputs: Any = None,
                **kwargs: Any,
            ) -> AsyncGenerator[AgentEvent | Msg, None]:
                """Count entries into the actual public Agent stream."""
                invoked.append(inputs)
                async for event in super().reply_stream(inputs, **kwargs):
                    yield event

        service = ChatService(
            storage=storage,
            workspace_manager=SimpleNamespace(
                get_workspace=AsyncMock(
                    return_value=SimpleNamespace(workdir=_WORKDIR),
                ),
            ),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=bus,
            resource_access_service=SimpleNamespace(
                resolve_agent=AsyncMock(return_value=record),
            ),
            custom_agent_cls=_ObservedAgent,
            tool_offload_timeout_secs=None,
        )
        return _ServiceFixture(service, storage, bus, invoked)

    async def _assert_interrupt(
        self,
        tool_state: str | None,
        *,
        stale: bool,
        through_retry: bool = False,
        channel_bound: bool = False,
    ) -> None:
        """Deliver an interrupt through real service assembly and execution."""
        fixture = self._fixture(tool_state)
        service, storage, bus = fixture.service, fixture.storage, fixture.bus
        user_id = storage.session.user_id
        session_id = storage.session.id
        agent_id = storage.session.agent_id
        message = storage.message
        original_state = storage.session.state.model_dump(mode="json")
        original_message = message.model_dump(mode="json")
        model = _NoCallModel()
        deliver = AsyncMock()
        if channel_bound:
            storage.session.origin = ChannelOrigin(
                channel_id="channel-1",
                chat_id="chat-1",
            )
            service._channel_clients = SimpleNamespace(
                get=AsyncMock(return_value=None),
                deliver=deliver,
            )
        notify = AsyncMock(wraps=service._notify_leader_of_failure)
        abandon = AsyncMock(wraps=_bus_ops.abandon_inbox_consumer)
        register = AsyncMock(wraps=_bus_ops.register_inbox_consumer)
        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=AsyncMock(return_value=Toolkit()),
            ),
            patch(
                "agentscope.app._service._chat.get_model",
                new=AsyncMock(return_value=model),
            ),
            patch.object(service, "_notify_leader_of_failure", new=notify),
            patch(
                "agentscope.app._service._chat.abandon_inbox_consumer",
                new=abandon,
            ),
            patch(
                "agentscope.app._service._chat.register_inbox_consumer",
                new=register,
            ),
        ):
            current_state = storage.session.state
            current_message = storage.message
            if stale:
                old_state = current_state.model_copy(deep=True)
                old_reply = old_state.context[-1]
                old_reply.id = "old-reply"
                if tool_state is not None:
                    old_reply.get_content_blocks("tool_call")[
                        0
                    ].id = "old-tool"
                old_state.reply_id = old_reply.id
                storage.session.state = old_state
                storage.message = old_reply
            if through_retry:
                await self._deliver_through_retry(
                    service,
                    storage,
                    bus,
                    current_state,
                    current_message,
                )
            else:
                try:
                    await service.interrupt(user_id, session_id, agent_id)
                finally:
                    storage.session.state = current_state
                    storage.message = current_message

                entries = await bus.queue_drain(
                    MessageBusKeys.wakeup_queue(),
                    max_count=2,
                )
                self.assertEqual(len(entries), 1)
                _entry_id, payload = entries[0]
                self.assertEqual(
                    payload["kind"],
                    MessageBusKeys.WAKEUP_KIND_RESUME,
                )
                queued_input = UserInterruptEvent.model_validate(
                    payload["input"],
                )
                self.assertEqual(
                    queued_input.reply_id,
                    "old-reply" if stale else message.id,
                )
                await service._run_impl(
                    user_id,
                    session_id,
                    agent_id,
                    queued_input,
                )

        self.assertEqual(model.calls, 0)
        self.assertFalse(
            await bus.is_locked(MessageBusKeys.session_lock(session_id)),
        )
        if stale or tool_state is None:
            deliver.assert_not_awaited()
            self.assertEqual(fixture.invoked, [])
            self.assertEqual(storage.get_message_calls, 0)
            self.assertEqual(storage.written_messages, [])
            self.assertEqual(bus.events, [])
            self.assertEqual(
                storage.session.state.model_dump(mode="json"),
                original_state,
            )
            self.assertEqual(
                storage.message.model_dump(mode="json"),
                original_message,
            )
            notify.assert_not_awaited()
            register.assert_not_awaited()
            abandon.assert_not_awaited()
        else:
            register.assert_awaited_once_with(bus, session_id)
            if channel_bound:
                deliver.assert_awaited_once_with(
                    session_id=session_id,
                    channel_id="channel-1",
                    chat_id="chat-1",
                    agent_id=agent_id,
                )
            self.assertEqual(len(fixture.invoked), 1)
            self.assertEqual(storage.get_message_calls, 1)
            self.assertEqual(
                storage.session.state.get_awaiting_tool_calls("agent"),
                [],
            )
            end_events = [e for e in bus.events if e["type"] == "REPLY_END"]
            self.assertEqual(len(end_events), 1)
            self.assertEqual(end_events[0]["reply_id"], message.id)
            self.assertEqual(end_events[0]["finished_reason"], "interrupted")
            self.assertEqual(storage.message.finished_reason, "interrupted")

    async def _deliver_through_retry(
        self,
        service: ChatService,
        storage: _Storage,
        bus: _RecordingBus,
        current_state: AgentState,
        current_message: Msg,
    ) -> None:
        """Pause a real retry while a locked session advances from A to B."""
        user_id = storage.session.user_id
        session_id = storage.session.id
        agent_id = storage.session.agent_id
        target = storage.session.state.reply_id
        lock_key = MessageBusKeys.session_lock(session_id)
        producer_waiting = asyncio.Event()
        allow_enqueue = asyncio.Event()
        retry_waiting = asyncio.Event()
        allow_retry = asyncio.Event()
        run_finished = asyncio.Event()
        registry = ChatRunRegistry()
        dispatcher = WakeupDispatcher(bus, storage, service, registry)
        pushes: list[str] = []
        delivered: list[object] = []
        spawned: list[asyncio.Task] = []
        queue_push = bus.queue_push
        spawn = registry.spawn
        run = service.run

        async def gated_push(
            key: str,
            payload: dict,
            **kwargs: Any,
        ) -> str:
            """Enqueue the captured interrupt only after the lock is held."""
            if key == MessageBusKeys.wakeup_queue() and (
                payload.get("kind") == MessageBusKeys.WAKEUP_KIND_RESUME
            ):
                if not pushes:
                    # The producer really observed an unlocked session.
                    # Hold it at enqueue while another run takes the lock.
                    producer_waiting.set()
                    await allow_enqueue.wait()
                    self.assertTrue(await bus.is_locked(lock_key))
                pushes.append(payload["input"]["reply_id"])
            return await queue_push(key, payload, **kwargs)

        async def gated_sleep(delay: float) -> None:
            """Pause this module's real retry timer at its backoff boundary."""
            if delay == wakeup_module._RESUME_RETRY_BACKOFF_SECS:
                retry_waiting.set()
                await allow_retry.wait()
            else:
                await asyncio.sleep(delay)

        def observe_spawn(coro: Any, **kwargs: Any) -> asyncio.Task:
            """Track real registry tasks without replacing their execution."""
            task = spawn(coro, **kwargs)
            spawned.append(task)
            return task

        async def observe_run(*args: Any, **kwargs: Any) -> None:
            """Record delivery and signal completion of the actual run."""
            delivered.append(kwargs["input_msg"])
            try:
                await run(*args, **kwargs)
            finally:
                run_finished.set()

        # Replace only this module's asyncio binding; all other users keep
        # the real sleep. The actual retry task and re-enqueue code run.
        clock = SimpleNamespace(**vars(asyncio))
        clock.sleep = gated_sleep
        producer: asyncio.Task | None = None
        with (
            patch.object(bus, "queue_push", new=gated_push),
            patch.object(registry, "spawn", new=observe_spawn),
            patch.object(service, "run", new=observe_run),
            patch.object(wakeup_module, "asyncio", new=clock),
        ):
            try:
                async with asyncio.timeout(5), registry, dispatcher:
                    producer = asyncio.create_task(
                        service.interrupt(user_id, session_id, agent_id),
                    )
                    await producer_waiting.wait()
                    async with bus.acquire_lock(lock_key):
                        allow_enqueue.set()
                        await producer
                        await retry_waiting.wait()
                        self.assertTrue(dispatcher._retry_tasks)
                        self.assertEqual(spawned, [])
                        self.assertEqual(delivered, [])
                        self.assertEqual(pushes, [target])
                        # Model another serialized run reaching parked B.
                        storage.session.state = current_state
                        storage.message = current_message
                    self.assertFalse(await bus.is_locked(lock_key))
                    allow_retry.set()
                    await run_finished.wait()
                    await asyncio.gather(*spawned)
                    self.assertEqual(pushes, [target, target])
                    self.assertEqual(len(spawned), 1)
                    self.assertEqual(len(delivered), 1)
                    self.assertIsInstance(delivered[0], UserInterruptEvent)
                    self.assertEqual(delivered[0].reply_id, target)
            finally:
                allow_enqueue.set()
                allow_retry.set()
                if producer is not None:
                    await asyncio.gather(producer, return_exceptions=True)

    async def test_interrupt_retry_preserves_target_across_session_lock(
        self,
    ) -> None:
        """A stale retry is dropped, while a same-target retry still works."""
        for tool_state in ("asking", "submitted"):
            for stale in (False, True):
                with self.subTest(tool_state=tool_state, stale=stale):
                    await self._assert_interrupt(
                        tool_state,
                        stale=stale,
                        through_retry=True,
                    )

    async def test_idle_interrupt_retry_does_not_publish_reply_events(
        self,
    ) -> None:
        """Even a matching retry is a silent no-op after work has finished."""
        for stale in (False, True):
            with self.subTest(stale=stale):
                await self._assert_interrupt(
                    None,
                    stale=stale,
                    through_retry=True,
                )

    async def test_stale_interrupt_does_not_touch_current_reply(self) -> None:
        """A delayed trigger leaves the current parked reply resumable."""
        for tool_state in ("asking", "submitted"):
            with self.subTest(tool_state=tool_state):
                await self._assert_interrupt(tool_state, stale=True)

    async def test_current_interrupt_still_closes_parked_reply(self) -> None:
        """The guard preserves interruption of either current HITL state."""
        for tool_state in ("asking", "submitted"):
            with self.subTest(tool_state=tool_state):
                await self._assert_interrupt(tool_state, stale=False)

    async def test_channel_interrupt_only_delivers_current_parked_reply(
        self,
    ) -> None:
        """Ignored interrupts must not start an outbound channel stream."""
        for tool_state in (None, "asking", "submitted"):
            for stale in (False, True):
                with self.subTest(tool_state=tool_state, stale=stale):
                    await self._assert_interrupt(
                        tool_state,
                        stale=stale,
                        channel_bound=True,
                    )

    async def test_stale_channel_interrupt_does_not_duplicate_resumed_reply(
        self,
    ) -> None:
        """An ignored trigger must not attach a reader to the next reply."""
        fixture = self._fixture("asking")
        service, storage, bus = fixture.service, fixture.storage, fixture.bus
        storage.session.origin = ChannelOrigin(
            channel_id="channel-1",
            chat_id="chat-1",
        )
        responses: list[list[dict]] = []

        async def record_response(
            _target: ChannelEvent,
            events: AsyncIterator[dict],
        ) -> None:
            """Drain each real delivery separately, as a platform would."""
            response: list[dict] = []
            responses.append(response)
            async for event in events:
                response.append(event)

        channel = SimpleNamespace(
            display_name="recording",
            chat_kind=AsyncMock(return_value=None),
            chat_name=AsyncMock(return_value="chat"),
            list_tools=AsyncMock(return_value=[]),
            send_response=record_response,
        )
        clients = ChannelClients(storage, bus, object())
        service._channel_clients = clients
        model = MockModel(
            stream=False,
            mock_chat_responses=[
                ChatResponse(
                    content=[TextBlock(text="Resumed answer.")],
                    is_last=True,
                ),
            ],
        )
        with (
            patch.object(clients, "get", new=AsyncMock(return_value=channel)),
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=AsyncMock(return_value=Toolkit()),
            ),
            patch(
                "agentscope.app._service._chat.get_model",
                new=AsyncMock(return_value=model),
            ),
        ):
            async with asyncio.timeout(5), clients:
                await service._run_impl(
                    storage.session.user_id,
                    storage.session.id,
                    storage.session.agent_id,
                    UserInterruptEvent(reply_id="old-reply"),
                )
                await service._run_impl(
                    storage.session.user_id,
                    storage.session.id,
                    storage.session.agent_id,
                    UserConfirmResultEvent(
                        reply_id=storage.message.id,
                        confirm_results=[
                            ConfirmResult(
                                confirmed=False,
                                tool_call=storage.message.get_content_blocks(
                                    "tool_call",
                                )[0],
                            ),
                        ],
                    ),
                )
                await asyncio.gather(*clients._deliveries)

        self.assertEqual(model.cnt, 1)
        self.assertEqual(len(responses), 1)
        self.assertEqual(
            [
                event["delta"]
                for event in responses[0]
                if event["type"] == "TEXT_BLOCK_DELTA"
            ],
            ["Resumed answer."],
        )
        self.assertEqual(
            [
                event["reply_id"]
                for event in responses[0]
                if event["type"] == "REPLY_END"
            ],
            [storage.message.id],
        )
