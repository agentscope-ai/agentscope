# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Reply-scoped interrupts must never cancel a successor reply."""
import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from service_cancel_dispatcher_test import _DropOnceBus
from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.app.deps import get_chat_service
from agentscope.app._manager import CancelDispatcher, ChatRunRegistry
from agentscope.app._router._session import session_router
from agentscope.app._service import ChatService
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.event import ReplyEndEvent, UserInterruptEvent
from agentscope.message import AssistantMsg, ToolCallBlock
from agentscope.state import AgentState
from agentscope.tool import Toolkit


class ReplyInterruptTest(IsolatedAsyncioTestCase):
    """Exercise the HTTP, bus, and final local cancellation boundary."""

    async def test_retry_after_202_during_parked_handoff(self) -> None:
        """An ineffective broadcast can be retried as a parked interrupt."""
        bus = InMemoryMessageBus()
        registry = ChatRunRegistry()
        dispatcher = CancelDispatcher(bus, registry, Mock())
        agent = Agent(
            name="a",
            system_prompt="Test agent.",
            model=MockModel(model="mock-model", stream=True),
            toolkit=Toolkit(),
            injection_config=InjectionConfig(inject_runtime_state=False),
            state=AgentState(
                session_id="s",
                reply_id="A",
                context=[
                    AssistantMsg(
                        name="a",
                        id="A",
                        content=[
                            ToolCallBlock(
                                id="tool",
                                name="confirm",
                                input="{}",
                                state="asking",
                            ),
                        ],
                    ),
                ],
            ),
        )
        session = SimpleNamespace(state=AgentState(session_id="s"))
        storage = SimpleNamespace(get_session=AsyncMock(return_value=session))
        service = ChatService(storage, Mock(), Mock(), Mock(), bus, Mock())
        app = FastAPI()
        app.include_router(session_router)
        app.dependency_overrides[get_chat_service] = lambda: service
        handoff = asyncio.Event()
        persist = asyncio.Event()
        delivered = asyncio.Event()
        interrupt = dispatcher._interrupt_session

        def receive(
            session_id: str,
            reply_id: str | None = None,
            queue_item_id: str | None = None,
        ) -> None:
            interrupt(
                session_id,
                reply_id=reply_id,
                queue_item_id=queue_item_id,
            )
            delivered.set()

        dispatcher._interrupt_session = receive

        async def park() -> None:
            async with bus.acquire_lock(MessageBusKeys.session_lock("s")):
                registry.set_reply("s", "A")
                registry.set_reply("s", None)
                handoff.set()
                await persist.wait()
                session.state = agent.state.model_copy(deep=True)

        async with bus, registry, dispatcher, AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            task = registry.spawn(park(), session_id="s")
            await asyncio.wait_for(handoff.wait(), 1)
            first = await client.post(
                "/sessions/s/interrupt?agent_id=a&reply_id=A",
                headers={"X-User-ID": "u"},
            )
            await asyncio.wait_for(delivered.wait(), 1)
            self.assertEqual(
                {
                    "response": (first.status_code, first.json()),
                    "cancelling": task.cancelling(),
                    "queued": await bus.queue_read(
                        MessageBusKeys.wakeup_queue(),
                    ),
                },
                {
                    "response": (202, {"session_id": "s"}),
                    "cancelling": 0,
                    "queued": [],
                },
            )
            persist.set()
            await asyncio.wait_for(task, 1)
            second = await client.post(
                "/sessions/s/interrupt?agent_id=a&reply_id=A",
                headers={"X-User-ID": "u"},
            )
            self.assertEqual(
                (second.status_code, second.json()),
                (202, {"session_id": "s"}),
            )
            queued = await bus.queue_drain(MessageBusKeys.wakeup_queue())
            self.assertEqual(len(queued), 1)
            payload = queued[0][1]
            self.assertEqual(
                {
                    key: payload[key]
                    for key in (
                        "user_id",
                        "session_id",
                        "agent_id",
                        "kind",
                    )
                },
                {
                    "user_id": "u",
                    "session_id": "s",
                    "agent_id": "a",
                    "kind": MessageBusKeys.WAKEUP_KIND_RESUME,
                },
            )
            control = UserInterruptEvent.model_validate(payload["input"])
            self.assertEqual(control.reply_id, "A")
            events = [event async for event in agent.reply_stream(control)]
            self.assertEqual(
                [
                    (event.reply_id, event.finished_reason)
                    for event in events
                    if isinstance(event, ReplyEndEvent)
                ],
                [("A", "interrupted")],
            )
            self.assertFalse(agent.state.has_awaiting_tool_calls("a"))

    async def test_delayed_interrupt_does_not_cancel_successor(self) -> None:
        """A targeted broadcast delivered during B must leave B intact."""
        bus = InMemoryMessageBus()
        registry = ChatRunRegistry()
        dispatcher = CancelDispatcher(bus, registry, Mock())
        reached = asyncio.Event()
        finish = asyncio.Event()
        outcomes = []

        async def run(reply_id: str) -> None:
            registry.set_reply("s", reply_id)
            reached.set()
            await finish.wait()
            outcomes.append(reply_id)

        async with bus, registry:
            first = registry.spawn(run("A"), session_id="s")
            await asyncio.wait_for(reached.wait(), 1)
            delayed_payload = {"session_id": "s", "reply_id": "A"}
            finish.set()
            await first
            reached.clear()
            finish.clear()
            second = registry.spawn(run("B"), session_id="s")
            await asyncio.wait_for(reached.wait(), 1)
            dispatcher._interrupt_session(**delayed_payload)
            dispatcher._interrupt_session(**delayed_payload)
            self.assertEqual(second.cancelling(), 0)
            finish.set()
            await second
        self.assertEqual(outcomes, ["A", "B"])

    async def test_same_task_tracks_each_reply(self) -> None:
        """One task can move from A to B without retaining A's identity."""
        registry = ChatRunRegistry()
        dispatcher = CancelDispatcher(Mock(), registry, Mock())
        ready = asyncio.Event()
        advance = asyncio.Event()
        finished = []

        async def run() -> None:
            registry.set_reply("s", "A")
            registry.set_reply("s", None)
            ready.set()
            await advance.wait()
            registry.set_reply("s", "B")
            ready.set()
            await asyncio.Event().wait()

        async with registry:
            task = registry.spawn(run(), session_id="s")
            await asyncio.wait_for(ready.wait(), 1)
            dispatcher._interrupt_session("s", reply_id="A")
            self.assertEqual(task.cancelling(), 0)
            ready.clear()
            advance.set()
            await asyncio.wait_for(ready.wait(), 1)
            dispatcher._interrupt_session("s", reply_id="A")
            self.assertEqual(task.cancelling(), 0)
            dispatcher._interrupt_session("s", reply_id="B")
            with self.assertRaises(asyncio.CancelledError):
                await task
            finished.append(task.cancelled())
        self.assertEqual(finished, [True])

    async def test_http_target_survives_delayed_broadcast(self) -> None:
        """The endpoint and bus must preserve A even when B runs later."""
        bus = InMemoryMessageBus()
        registry = ChatRunRegistry()
        dispatcher = CancelDispatcher(bus, registry, Mock())
        storage = SimpleNamespace(
            get_session=AsyncMock(
                return_value=SimpleNamespace(
                    state=SimpleNamespace(reply_id="B"),
                ),
            ),
        )
        service = ChatService(storage, Mock(), Mock(), Mock(), bus, Mock())
        app = FastAPI()
        app.include_router(session_router)
        app.dependency_overrides[get_chat_service] = lambda: service
        ready = asyncio.Event()
        finish = asyncio.Event()
        results = []

        async def run() -> None:
            registry.set_reply("s", "B")
            ready.set()
            await finish.wait()
            results.append("B completed")

        async with bus, registry:
            task = registry.spawn(run(), session_id="s")
            await asyncio.wait_for(ready.wait(), 1)
            async with (
                bus.acquire_lock(MessageBusKeys.session_lock("s")),
                AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://test",
                ) as client,
            ):
                with patch.object(bus, "publish", new=AsyncMock()) as send:
                    response = await client.post(
                        "/sessions/s/interrupt",
                        params={"agent_id": "a", "reply_id": "A"},
                        headers={"X-User-ID": "u"},
                    )
                    self.assertEqual(
                        (response.status_code, response.json()),
                        (202, {"session_id": "s"}),
                    )
                    send.assert_awaited_once_with(
                        MessageBusKeys.session_interrupt_channel(),
                        {"session_id": "s", "reply_id": "A"},
                    )
                    payload = send.await_args.args[1]
                    invalid = await client.post(
                        "/sessions/s/interrupt",
                        params={"agent_id": "a", "reply_id": ""},
                        headers={"X-User-ID": "u"},
                    )
                    self.assertEqual(invalid.status_code, 422)
                    self.assertEqual(send.await_count, 1)
                dispatcher._interrupt_session(**payload)
                self.assertEqual(task.cancelling(), 0)
                finish.set()
                await task
        self.assertEqual(results, ["B completed"])

    async def test_parked_interrupt_preserves_requested_reply(self) -> None:
        """A queued control must not substitute the latest parked reply."""
        storage = SimpleNamespace(
            get_session=AsyncMock(
                return_value=SimpleNamespace(
                    state=SimpleNamespace(reply_id="B"),
                ),
            ),
        )
        bus = SimpleNamespace(is_locked=AsyncMock(return_value=False))
        service = ChatService(storage, Mock(), Mock(), Mock(), bus, Mock())
        with patch(
            "agentscope.app._service._chat.enqueue_run_trigger",
            new=AsyncMock(),
        ) as enqueue:
            await service.interrupt("u", "s", "a", reply_id="A")
        self.assertEqual(
            enqueue.await_args.kwargs["inputs"].reply_id,
            "A",
        )
        self.assertIsInstance(
            enqueue.await_args.kwargs["inputs"],
            UserInterruptEvent,
        )

    async def test_stale_queued_interrupt_is_dropped_under_lock(self) -> None:
        """A resume queued for A must not even assemble the later B."""
        bus = InMemoryMessageBus()
        locked = []

        async def get_session(*_args: object) -> object:
            locked.append(
                await bus.is_locked(MessageBusKeys.session_lock("s")),
            )
            return SimpleNamespace(
                state=SimpleNamespace(
                    reply_id="B",
                    has_awaiting_tool_calls=Mock(return_value=False),
                ),
            )

        storage = SimpleNamespace(get_session=get_session)
        access = SimpleNamespace(resolve_agent=AsyncMock(return_value=Mock()))
        service = ChatService(storage, Mock(), Mock(), Mock(), bus, access)
        async with bus:
            with patch(
                "agentscope.app._service._chat.get_model",
                new=AsyncMock(),
            ) as get_model:
                await service._run_impl(
                    "u",
                    "s",
                    "a",
                    UserInterruptEvent(reply_id="A"),
                )
                await service._run_impl(
                    "u",
                    "s",
                    "a",
                    UserInterruptEvent(reply_id="B"),
                )
                get_model.assert_not_awaited()
            self.assertEqual(
                {
                    "loaded_under_lock": locked,
                    "events": await bus.log_read(
                        MessageBusKeys.session_events("s"),
                    ),
                },
                {"loaded_under_lock": [True, True], "events": []},
            )

    async def test_legacy_interrupt_keeps_session_scope(self) -> None:
        """Existing callers without a reply target keep their behavior."""
        bus = SimpleNamespace(
            is_locked=AsyncMock(return_value=True),
            publish=AsyncMock(),
        )
        storage = SimpleNamespace(get_session=AsyncMock(return_value=Mock()))
        service = ChatService(storage, Mock(), Mock(), Mock(), bus, Mock())
        await service.interrupt("u", "s", "a")
        bus.publish.assert_awaited_once_with(
            MessageBusKeys.session_interrupt_channel(),
            {"session_id": "s"},
        )

    async def test_bus_target_is_enforced_by_worker(self) -> None:
        """Stale and malformed targets never become session-wide cancels."""
        async with InMemoryMessageBus() as bus:
            await self._assert_bus_target(bus)

    async def test_bus_target_survives_subscription_reconnect(self) -> None:
        """Reconnect after EOF or failure without losing reply filtering."""
        for mode in ("end", "raise"):
            with self.subTest(mode=mode), patch(
                "agentscope.app._manager._cancel_dispatcher."
                "_SUBSCRIBE_BACKOFF_INITIAL_SECS",
                0.001,
            ):
                await self._assert_bus_target(_DropOnceBus(mode))

    async def _assert_bus_target(
        self,
        bus: InMemoryMessageBus | _DropOnceBus,
    ) -> None:
        """Deliver invalid, stale and matching targets to a live worker."""
        registry = ChatRunRegistry()
        dispatcher = CancelDispatcher(bus, registry, Mock())
        ready = asyncio.Event()
        consumed = asyncio.Event()
        observed = []
        interrupt = dispatcher._interrupt_session

        def receive(
            session_id: str,
            reply_id: str | None = None,
            queue_item_id: str | None = None,
        ) -> None:
            interrupt(
                session_id,
                reply_id=reply_id,
                queue_item_id=queue_item_id,
            )
            observed.append((session_id, reply_id))
            if session_id == "barrier":
                consumed.set()

        async def run() -> None:
            registry.set_reply("s", "B")
            ready.set()
            await asyncio.Event().wait()

        dispatcher._interrupt_session = receive
        async with registry, dispatcher:
            if isinstance(bus, _DropOnceBus):
                await asyncio.wait_for(
                    bus.resubscribed[
                        MessageBusKeys.session_interrupt_channel()
                    ].wait(),
                    1,
                )
            task = registry.spawn(run(), session_id="s")
            await asyncio.wait_for(ready.wait(), 1)
            for target in ("A", None, "", 42):
                await bus.publish(
                    MessageBusKeys.session_interrupt_channel(),
                    {"session_id": "s", "reply_id": target},
                )
            await bus.publish(
                MessageBusKeys.session_interrupt_channel(),
                {"session_id": "barrier", "reply_id": "A"},
            )
            await asyncio.wait_for(consumed.wait(), 1)
            self.assertEqual(
                {"targets": observed, "cancellations": task.cancelling()},
                {
                    "targets": [("s", "A"), ("barrier", "A")],
                    "cancellations": 0,
                },
            )
            await bus.publish(
                MessageBusKeys.session_interrupt_channel(),
                {"session_id": "s", "reply_id": "B"},
            )
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
