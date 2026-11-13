# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Multiple turns keep their own streams while platform sends stay ordered."""
import asyncio
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import service_chat_channel_delivery_test as fixture
from service_chat_channel_delivery_test import (
    _RecordingChannel,
    _Storage,
    _WorkspaceManager,
)
from agentscope.app._service import ChatService
from agentscope.app._bus_ops import deliver_to_inbox, publish_session_event
from agentscope.app.channel import ChannelClients, ChannelTypeRegistry
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import ChannelOrigin
from agentscope.event import (
    ConfirmResult,
    ReplyStartEvent,
    ReplyEndEvent,
    RequireUserConfirmEvent,
    RequireExternalExecutionEvent,
    UserConfirmResultEvent,
    ExternalExecutionResultEvent,
)
from agentscope.message import (
    AssistantMsg,
    HintBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
)
from agentscope.types import ReplyFinishedReason


class _PersistentStorage(_Storage):
    """Keep detached state and replies for a subsequent real service run."""

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.messages: dict[str, Any] = {}

    async def upsert_message(
        self,
        _user: str,
        _session: str,
        msg: Any,
    ) -> None:
        """Persist a detached message."""
        self.messages[msg.id] = msg.model_copy(deep=True)

    async def get_message(self, _user: str, _session: str, mid: str) -> Any:
        """Load a detached reply for continuation."""
        return self.messages[mid].model_copy(deep=True)

    async def update_session_state(self, *, state: Any, **_: Any) -> None:
        self.session.state = state.model_copy(deep=True)


class MultiReplyTest(IsolatedAsyncioTestCase):
    """Real service loop, delivery runtime and bus; fake I/O only."""

    async def asyncSetUp(self) -> None:
        helper = fixture.ChannelDeliveryFromTheRunTest()
        self.user, self.record, self.session, channel = helper._fixture(
            ChannelOrigin(channel_id="chan-1", chat_id="chat-1"),
        )
        self.storage = _PersistentStorage(self.session, self.record, channel)
        self.bus = InMemoryMessageBus()
        self.addAsyncCleanup(self.bus.aclose)
        self.generated: list[str] = []
        self.sent: list[list[dict]] = []
        self.completed: list[int] = []
        self.read_gate = asyncio.Event()
        self.read_gate.set()
        self.finish_gate = asyncio.Event()
        self.finish_gate.set()
        self.first_drained = asyncio.Event()
        self.agent_started = asyncio.Event()
        self.mode = "normal"
        self.turns = 2
        self.park: str | None = None
        owner = self

        class Channel(_RecordingChannel):
            """Can delay reads or final sends without blocking the agent."""

            async def send_response(self, event: Any, events: Any) -> None:
                """Record a platform call and optionally delay completion."""
                index = len(owner.sent)
                batch: list[dict] = []
                owner.sent.append(batch)
                if owner.mode == "early" and index == 0:
                    return
                if owner.mode == "error" and index == 0:
                    raise RuntimeError("fake platform failure")
                await owner.read_gate.wait()
                async for raw in events:
                    batch.append(raw)
                if index == 0:
                    owner.first_drained.set()
                    await owner.finish_gate.wait()
                owner.completed.append(index)

        class Agent:
            """Generate turns and leave inbox work after each final drain."""

            def __init__(self, *, state: Any, name: str, **_: Any) -> None:
                self.state, self.name = state, name

            async def reply_stream(self, inputs: Any) -> Any:
                """Yield reply segments with real parked state."""
                if owner.mode == "before_start_error":
                    raise RuntimeError("fake agent failure before reply")
                checkpoint = await owner.bus.registry_get(
                    MessageBusKeys.session_event_checkpoint(owner.session.id),
                    MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
                )
                if len(owner.generated) < owner.turns:
                    owner.assertEqual(checkpoint, owner.initial_cursor)
                if isinstance(
                    inputs,
                    (
                        UserConfirmResultEvent,
                        ExternalExecutionResultEvent,
                    ),
                ):
                    rid = self.state.reply_id
                    self.state.context = []
                    owner.park = None
                else:
                    if inputs is None:
                        drained = await owner.bus.queue_drain(
                            MessageBusKeys.inbox(owner.session.id),
                        )
                        owner.assertEqual(len(drained), 1)
                    rid = f"r-{len(owner.generated) + 1}"
                    owner.generated.append(rid)
                    self.state.reply_id = rid
                    yield ReplyStartEvent(
                        reply_id=rid,
                        name=self.name,
                        session_id=owner.session.id,
                    )
                if owner.mode == "cancel":
                    owner.agent_started.set()
                    await asyncio.Event().wait()
                if owner.park:
                    tool_state = (
                        "asking" if owner.park == "confirm" else "submitted"
                    )
                    self.state.context = [
                        AssistantMsg(
                            name=self.name,
                            content=[
                                ToolCallBlock(
                                    id="tool",
                                    name="tool",
                                    input="{}",
                                    state=tool_state,
                                ),
                            ],
                        ),
                    ]
                    event_cls = (
                        RequireUserConfirmEvent
                        if owner.park == "confirm"
                        else RequireExternalExecutionEvent
                    )
                    yield event_cls(
                        reply_id=rid,
                        tool_calls=[],
                        session_id=owner.session.id,
                        name=self.name,
                    )
                    return
                if len(owner.generated) < owner.turns:
                    await deliver_to_inbox(
                        owner.bus,
                        user_id=owner.user,
                        session_id=owner.session.id,
                        agent_id=owner.record.id,
                        payload=HintBlock(
                            source="user",
                            hint="next",
                        ).model_dump(mode="json"),
                    )
                yield ReplyEndEvent(
                    reply_id=rid,
                    session_id=owner.session.id,
                    finished_reason=ReplyFinishedReason.COMPLETED,
                )

        class Access:
            """Resolve the fixture's agent without external access."""

            async def resolve_agent(self, *_: Any) -> Any:
                """Return the owned agent record."""
                return owner.record.model_copy(deep=True)

        async def inert(*_: Any, **__: Any) -> object:
            return object()

        self.clients = ChannelClients(
            self.storage,
            self.bus,
            ChannelTypeRegistry([Channel]),
        )
        self.addAsyncCleanup(self.clients.__aexit__, None, None, None)
        self.service = ChatService(
            storage=self.storage,
            workspace_manager=_WorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=self.bus,
            resource_access_service=Access(),
            custom_agent_cls=Agent,
            channel_clients=self.clients,
        )
        for name in ("get_model", "get_toolkit"):
            patcher = patch(f"agentscope.app._service._chat.{name}", new=inert)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.initial_cursor = await publish_session_event(
            self.bus,
            self.session.id,
            ReplyEndEvent(
                reply_id="old",
                session_id=self.session.id,
                finished_reason=ReplyFinishedReason.COMPLETED,
            ).model_dump(mode="json"),
        )
        await self.bus.registry_set(
            MessageBusKeys.session_event_checkpoint(self.session.id),
            MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
            self.initial_cursor,
        )

    async def run_service(self, inputs: Any = "user") -> None:
        """Use a timeout only as a deadlock guard, never for ordering."""
        if inputs == "user":
            inputs = UserMsg(name="user", content="hello")
        await asyncio.wait_for(
            self.service._run_impl(
                self.user,
                self.session.id,
                self.record.id,
                inputs,
            ),
            5,
        )

    async def finish_sends(self) -> None:
        """Join owned background deliveries and verify subscription cleanup."""
        await asyncio.wait_for(
            asyncio.gather(*list(self.clients._deliveries)),
            5,
        )
        self.assertFalse(self.clients._streams)
        self.assertFalse(self.clients._send_tails)
        self.assertFalse(any(self.bus._subscribers.values()))

    def starts(self) -> list[str]:
        """Collect distinct turn starts in platform call order."""
        return [
            raw["reply_id"]
            for batch in self.sent
            for raw in batch
            if raw["type"] == "REPLY_START"
        ]

    async def test_two_and_three_replies(self) -> None:
        """Local cursor advances while persisted state/checkpoint do not."""
        for count in (2, 3):
            with self.subTest(turns=count):
                self.turns = len(self.generated) + count
                self.initial_cursor = await self.bus.registry_get(
                    MessageBusKeys.session_event_checkpoint(self.session.id),
                    MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
                )
                await self.run_service()
                await self.finish_sends()
                self.assertEqual(self.starts(), self.generated)
                for batch in self.sent:
                    self.assertEqual(
                        [raw["type"] for raw in batch],
                        ["REPLY_START", "REPLY_END"],
                    )
                self.assertTrue(
                    all(
                        rid in self.storage.messages for rid in self.generated
                    ),
                )

    async def test_delayed_read_after_trim_and_following_run(self) -> None:
        """Buffered streams survive trim and cannot consume the next run."""
        self.read_gate.clear()
        await self.run_service()
        self.assertEqual(self.generated, ["r-1", "r-2"])
        await self.run_service()
        self.read_gate.set()
        await self.finish_sends()
        self.assertEqual(self.starts(), ["r-1", "r-2", "r-3"])
        self.assertEqual(self.completed, [0, 1, 2])

    async def test_final_send_does_not_block_producer_or_get_overtaken(
        self,
    ) -> None:
        """Later turns finish while the first platform send is blocked."""
        self.turns = 3
        self.finish_gate.clear()
        await self.run_service()
        await asyncio.wait_for(self.first_drained.wait(), 5)
        self.assertEqual(self.generated, ["r-1", "r-2", "r-3"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.completed, [])
        self.finish_gate.set()
        await self.finish_sends()
        self.assertEqual(self.starts(), self.generated)
        self.assertEqual(self.completed, [0, 1, 2])

    async def test_platform_error_and_early_return_release_readers(
        self,
    ) -> None:
        """A failed send must not strand its successor or subscription."""
        for mode in ("error", "early"):
            with self.subTest(mode=mode):
                self.mode = mode
                self.sent.clear()
                self.turns = len(self.generated) + 2
                self.initial_cursor = await self.bus.registry_get(
                    MessageBusKeys.session_event_checkpoint(self.session.id),
                    MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
                )
                await self.run_service()
                await self.finish_sends()
                self.assertEqual(len(self.sent), 2)
                self.assertEqual(self.starts(), [self.generated[-1]])

    async def test_parked_terminals_resume_the_same_reply(self) -> None:
        """A legal resume opens a new segment of the parked reply."""
        self.turns = 1
        for kind in ("confirm", "external"):
            with self.subTest(kind=kind):
                self.park = kind
                await self.run_service()
                await self.finish_sends()
                rid = self.generated[-1]
                before = len(self.sent)
                await self.run_service(None)
                self.assertEqual(len(self.sent), before)
                event_cls = (
                    UserConfirmResultEvent
                    if kind == "confirm"
                    else ExternalExecutionResultEvent
                )
                tool_call = self.storage.session.state.context[-1].content[0]
                results = (
                    {
                        "confirm_results": [
                            ConfirmResult(
                                confirmed=True,
                                tool_call=tool_call,
                            ),
                        ],
                    }
                    if kind == "confirm"
                    else {
                        "execution_results": [
                            ToolResultBlock(
                                id="tool",
                                name="tool",
                                output="done",
                            ),
                        ],
                    }
                )
                await self.run_service(event_cls(reply_id=rid, **results))
                await self.finish_sends()
                self.assertEqual(
                    self.sent[-1][0]["type"],
                    "USER_CONFIRM_RESULT"
                    if kind == "confirm"
                    else "EXTERNAL_EXECUTION_RESULT",
                )
                self.assertEqual(self.sent[-1][-1]["type"], "REPLY_END")
                self.assertEqual(self.starts()[-2:], [rid, rid])

    async def test_failure_before_reply_still_delivers_error(self) -> None:
        """Keep a successfully published error reply available to delivery."""
        self.mode = "before_start_error"
        await self.run_service()
        await self.finish_sends()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(
            [raw["type"] for raw in self.sent[0]],
            ["REPLY_START", "REPLY_END"],
        )
        self.assertEqual(
            self.sent[0][-1]["finished_reason"],
            ReplyFinishedReason.ERROR,
        )

    async def test_failed_terminal_publish_cannot_consume_following_run(
        self,
    ) -> None:
        """A missing terminal must not let a reader take the next reply."""
        for mode in ("normal", "before_start_error"):
            with self.subTest(mode=mode):
                self.mode = mode
                self.turns = len(self.generated) + 1
                self.initial_cursor = await self.bus.registry_get(
                    MessageBusKeys.session_event_checkpoint(self.session.id),
                    MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
                )
                before = len(self.sent)

                async def fail_terminal(
                    bus: Any,
                    session_id: str,
                    event: dict,
                ) -> str:
                    if self.mode == "before_start_error" or (
                        event["type"] == "REPLY_END"
                    ):
                        raise RuntimeError("fake terminal publication failure")
                    return await publish_session_event(bus, session_id, event)

                with patch(
                    "agentscope.app._service._chat.publish_session_event",
                    new=fail_terminal,
                ):
                    await self.run_service()

                self.mode = "normal"
                self.turns = len(self.generated) + 1
                self.initial_cursor = await self.bus.registry_get(
                    MessageBusKeys.session_event_checkpoint(self.session.id),
                    MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
                )
                await self.run_service()
                await self.finish_sends()
                batches = self.sent[before:]
                self.assertEqual(len(batches), 2)
                self.assertIn(
                    [raw["type"] for raw in batches[0]],
                    ([], ["REPLY_START"]) if mode == "normal" else ([],),
                )
                self.assertTrue(
                    all(
                        raw["reply_id"] != self.generated[-1]
                        for raw in batches[0]
                    ),
                )
                self.assertEqual(
                    [raw["type"] for raw in batches[1]],
                    ["REPLY_START", "REPLY_END"],
                )
                self.assertEqual(
                    batches[1][0]["reply_id"],
                    self.generated[-1],
                )

    async def test_cancelled_producer_cannot_consume_following_run(
        self,
    ) -> None:
        """Cancellation before a terminal event abandons the current reader."""
        self.mode = "cancel"
        task = asyncio.create_task(self.run_service())
        await asyncio.wait_for(self.agent_started.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await self.finish_sends()
        self.mode = "normal"
        self.turns = len(self.generated)
        await self.run_service()
        await self.finish_sends()
        self.assertEqual(self.starts(), ["r-1", "r-2"])
