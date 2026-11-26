# -*- coding: utf-8 -*-
# pylint: disable=protected-access, using-constant-test
"""A channel-bound run hands its reply to the channel runtime.

The run's own node delivers the reply now, so what matters is that the
run starts a delivery aimed at the right chat, and that the delivery
still sees the whole reply when the run finishes before the channel
starts reading — which is the normal case, a platform call being slower
than the agent's last event.
"""
import asyncio
import json
from contextlib import aclosing
from types import SimpleNamespace
from typing import Any, AsyncGenerator, AsyncIterator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel, ValidationError
from utils import MockModel

from agentscope.agent import Agent, ContextConfig, ReActConfig
from agentscope.app._bus_ops import (
    register_inbox_consumer,
    has_pending_inbox_or_release,
    session_execution,
    list_chat_inputs,
    update_chat_input,
)
from agentscope.app._client_external_tool import (
    ClientExternalTool,
    ClientExternalToolDefinition,
)
from agentscope.app._manager._chat_input_queue import ChatInputQueue
from agentscope.app._router._chat import chat
from agentscope.app._router._schema._chat import ChatRequest
from agentscope.app._router._schema._session import UpdateSessionRequest
from agentscope.app._router._session import (
    stream_session_events,
    update_session,
)
from agentscope.app._service import ChatService, SubagentHitlProjector
from agentscope.app.channel import (
    ChannelBase,
    ChannelClients,
    ChannelEvent,
    ChannelStatus,
    ChannelTypeRegistry,
    ChatKind,
    FeishuChannel,
)
from agentscope.app.channel._base import _EVENT_ADAPTER
from agentscope.app.channel._gateway import ChannelGateway
from agentscope.app.channel._routing import resolve
from agentscope.app.channel._approval import (
    CHANNEL_APPROVAL_UNAVAILABLE,
    load_approval,
    load_reply_requester,
    remember_reply_requester,
)
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.event import (
    ExternalExecutionResultEvent,
    ConfirmResult,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireUserConfirmEvent,
    RequireExternalExecutionEvent,
    TextBlockStartEvent,
    TextBlockDeltaEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.types import ReplyFinishedReason
from agentscope.message import (
    AssistantMsg,
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.state import AgentState
from agentscope.tool import AskUser, FunctionTool, Toolkit
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChannelBinding,
    ChannelRecord,
    ChatModelConfig,
    RoutingConfig,
    SessionConfig,
    SessionRecord,
    SessionSettings,
    TeamData,
    TeamMember,
    TeamRecord,
    ChannelOrigin,
    SessionOrigin,
    UserOrigin,
)


class _RecordingChannel(ChannelBase):
    """Captures the send target and the events it was fed."""

    channel_type = "fake"
    display_name = "Fake"
    platform_bot_id_field = "bot_id"
    instances: list["_RecordingChannel"] = []
    tool_user_ids: list[str | None] = []

    class Credentials(BaseModel):
        """Credentials for the fake platform."""

        bot_id: str

    class Config(BaseModel):
        """Options for the fake platform."""

    def __init__(
        self,
        channel_id: str,
        credentials: "Credentials",
        config: "Config",  # pylint: disable=unused-argument
    ) -> None:
        """Register this instance so the test can inspect it."""
        del credentials
        self._channel_id = channel_id
        self.status = ChannelStatus()
        self.target: ChannelEvent | None = None
        self.seen: list[str] = []
        self.done = asyncio.Event()
        _RecordingChannel.instances.append(self)

    @property
    def channel_id(self) -> str:
        """The unique channel instance identifier."""
        return self._channel_id

    async def start_listening(  # pylint: disable=unused-argument
        self,
        emit: Any,
    ) -> None:
        """Unused: this test only exercises the outbound half."""

    async def send_response(
        self,
        event: ChannelEvent,
        events: AsyncIterator[dict],
    ) -> None:
        """Record the target, then drain the run's events."""
        self.target = event
        async for evt in events:
            self.seen.append(evt.get("type", ""))
        self.done.set()

    async def list_tools(
        self,
        workspace: object,
        channel_user_id: str | None = None,
    ) -> list:
        """Record the trusted user the service equips tools with."""
        del workspace
        self.tool_user_ids.append(channel_user_id)
        return []

    async def chat_kind(self, chat_id: str) -> ChatKind | None:
        """Classify a chat by an id prefix the tests control."""
        if chat_id.startswith("private:"):
            return ChatKind.PRIVATE
        if chat_id.startswith("group:"):
            return ChatKind.GROUP
        return None


class _Storage:
    """Serve one channel-bound session, its agent, and its channel."""

    def __init__(
        self,
        session: SessionRecord,
        agent: AgentRecord,
        channel: ChannelRecord,
    ) -> None:
        self.session = session
        self.agent = agent
        self.channel = channel
        self.messages: dict[str, Msg] = {}

    async def get_session(self, *_: object, **__: object) -> SessionRecord:
        """Return a detached copy."""
        return self.session.model_copy(deep=True)

    async def get_agent(self, *_: object, **__: object) -> AgentRecord:
        """Return a detached copy."""
        return self.agent.model_copy(deep=True)

    async def get_channel(self, *_: object, **__: object) -> ChannelRecord:
        """Return the one channel this stub serves."""
        return self.channel

    async def update_session_state(
        self,
        *,
        state: AgentState,
        **_: object,
    ) -> None:
        """Persist an independent snapshot for the next run."""
        self.session.state = state.model_copy(deep=True)

    async def upsert_message(
        self,
        user_id: str,
        session_id: str,
        message: Msg,
    ) -> None:
        """Keep the reply so Web UI decisions can resume it."""
        del user_id, session_id
        self.messages[message.id] = message.model_copy(deep=True)

    async def get_message(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> Msg | None:
        """Load the reply accumulated by earlier runs."""
        del user_id, session_id
        message = self.messages.get(message_id)
        return message.model_copy(deep=True) if message else None


class _WorkspaceManager:
    """Return a minimal workspace handle."""

    async def get_workspace(self, *_: object, **__: object) -> object:
        """Return an inert workspace."""
        return SimpleNamespace(workdir="/tmp/agentscope-delivery-test")


class _TwoApprovalAgent:
    """Pause on two successive tools without calling a model."""

    pause_event = RequireUserConfirmEvent

    def __init__(self, *, name: str, state: AgentState, **_: object) -> None:
        self.name = name
        self.state = state

    async def reply_stream(
        self,
        inputs: Msg | UserConfirmResultEvent,
    ) -> AsyncGenerator[object, None]:
        """A Web approval advances to the next tool, then completion."""
        if isinstance(inputs, Msg):
            self.state.reply_id = "r-1"
            tool_id = "tool-1"
            yield ReplyStartEvent(
                session_id=self.state.session_id,
                reply_id="r-1",
                name=self.name,
            )
            yield TextBlockStartEvent(reply_id="r-1", block_id="text-1")
            yield TextBlockDeltaEvent(
                reply_id="r-1",
                block_id="text-1",
                delta="Preparing the first tool.",
            )
        elif inputs.confirm_results[0].tool_call.id == "tool-1":
            tool_id = "tool-2"
        else:
            self.state.context = []
            yield ReplyEndEvent(
                session_id=self.state.session_id,
                reply_id=self.state.reply_id,
                name=self.name,
                finished_reason=ReplyFinishedReason.COMPLETED,
            )
            return
        tool = ToolCallBlock(
            id=tool_id,
            name="Bash",
            input="{}",
            state="submitted"
            if self.pause_event is RequireExternalExecutionEvent
            else "asking",
        )
        self.state.context = [inputs] if isinstance(inputs, Msg) else []
        self.state.context.append(
            AssistantMsg(id="r-1", name=self.name, content=[tool]),
        )
        if self.pause_event is RequireUserConfirmEvent:
            prompt = RequireUserConfirmEvent.issue("r-1", tool)
            self.state.context[-1].set_confirmation_request(tool.id, prompt.id)
            yield prompt
        else:
            yield self.pause_event(reply_id="r-1", tool_calls=[tool])


class _ChannelDeliveryFixture(IsolatedAsyncioTestCase):
    """Build isolated chat services and channel clients."""

    def setUp(self) -> None:
        """Isolate the instances each test observes."""
        _RecordingChannel.instances.clear()
        _RecordingChannel.tool_user_ids.clear()

    def _fixture(self, source: SessionOrigin) -> tuple:
        """Build a session of ``source`` plus its agent and channel."""
        user_id = "user-1"
        agent = AgentRecord(
            id="agent-1",
            user_id=user_id,
            data=AgentData(
                name="a",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        session = SessionRecord(
            id="session-1",
            user_id=user_id,
            agent_id=agent.id,
            origin=source,
            config=SessionConfig(
                workspace_id="ws-1",
                chat_model_config=ChatModelConfig(
                    type="test",
                    credential_id="cred-1",
                    model="m",
                    parameters={},
                ),
            ),
        )
        channel = ChannelRecord(
            id="chan-1",
            channel_type="fake",
            user_id=user_id,
            credentials={"bot_id": "bot-1"},
            routing=RoutingConfig(
                bindings=[ChannelBinding(match_value="*", agent_id=agent.id)],
            ),
            session=SessionSettings(chat_model_config={"type": "test"}),
        )
        return user_id, agent, session, channel

    def _approval_fixture(self) -> tuple:
        """Use the real Feishu delivery path with platform I/O stubbed."""
        _, agent, session, channel = self._fixture(
            ChannelOrigin(channel_id="chan-1", chat_id="chat-1"),
        )
        channel.channel_type = "feishu"
        channel.credentials = {"app_id": "app", "app_secret": "secret"}
        storage = _Storage(session, agent, channel)
        bus = InMemoryMessageBus()
        clients = ChannelClients(
            storage,
            bus,
            ChannelTypeRegistry([FeishuChannel]),
        )
        self.addAsyncCleanup(clients.__aexit__, None, None, None)
        service = ChatService(
            storage=storage,
            workspace_manager=_WorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=bus,
            resource_access_service=SimpleNamespace(
                resolve_agent=AsyncMock(return_value=agent),
            ),
            custom_agent_cls=_TwoApprovalAgent,
            channel_clients=clients,
        )
        for target in ("get_toolkit", "get_model"):
            self.enterContext(
                patch(
                    f"agentscope.app._service._chat.{target}",
                    new=AsyncMock(return_value=object()),
                ),
            )
        for target, value in (
            ("chat_kind", ChatKind.GROUP),
            ("chat_name", "Test chat"),
            ("list_tools", []),
            ("_card_push", None),
            ("_close_stream", None),
        ):
            self.enterContext(
                patch.object(
                    FeishuChannel,
                    target,
                    new=AsyncMock(return_value=value),
                ),
            )
        return service, clients, storage, bus


class MemberApprovalRetryTest(_ChannelDeliveryFixture):
    """A parked member can notify its connected leader after an outage."""

    async def test_failed_member_card_retries_on_connected_leader(
        self,
    ) -> None:
        """A live subscriber receives the retry without another turn."""
        await self._check_member_card_retry(resolved=False)

    async def test_failed_member_card_rechecks_worker_state(self) -> None:
        """A card resolved during backoff is closed on the live stream."""
        await self._check_member_card_retry(resolved=True)

    async def _check_member_card_retry(self, *, resolved: bool) -> None:
        service, _, storage, bus = self._approval_fixture()
        service._channel_clients = None
        worker = storage.session
        worker.team_id = "team"
        team = SimpleNamespace(session_id="leader", leader_agent_id="boss")
        self.enterContext(
            patch.object(
                storage,
                "get_team",
                AsyncMock(return_value=team),
                create=True,
            ),
        )
        response = await stream_session_events(
            "leader",
            "boss",
            None,
            worker.user_id,
            storage,
            bus,
        )
        publish = service._projection.publish
        missed = []

        async def fail_first(sid: str, name: str, card: dict) -> None:
            if not missed:
                missed.append(card)
                if resolved:
                    # Another node has committed the end of this reply.
                    worker.state = AgentState()
                raise ConnectionError("first notification failed")
            await publish(sid, name, card)

        with patch.object(service._projection, "publish", fail_first):
            async with aclosing(response.body_iterator) as stream:
                first = json.loads(
                    (await anext(stream)).removeprefix("data: "),
                )
                self.assertEqual(first["value"], {"pending": []})
                await service._run_impl(
                    worker.user_id,
                    worker.id,
                    worker.agent_id,
                    UserMsg("user", "Start"),
                )
                (card,) = missed
                frame = await asyncio.wait_for(anext(stream), 3)
                event = json.loads(frame.removeprefix("data: "))
        expected = (
            (
                SubagentHitlProjector.EVT_RESULT,
                {
                    k: card[k]
                    for k in ("worker_session_id", "reply_id", "request_id")
                },
            )
            if resolved
            else (SubagentHitlProjector.EVT_REQUIRE, card)
        )
        self.assertEqual((event["name"], event["value"]), expected)


class ChannelDeliveryFromTheRunTest(_ChannelDeliveryFixture):
    """The run starts the delivery; the channel runtime owns it."""

    async def test_member_approvals_survive_reconnect_independently(
        self,
    ) -> None:
        """Reconnect during a save, then answer two prompts out of order."""
        service, _, storage, bus = self._approval_fixture()
        service._agent_cls = Agent
        service._channel_clients = None
        ids = ("user-1", "session-1", "agent-1")
        storage.session.team_id = "team"
        team = SimpleNamespace(session_id="leader", leader_agent_id="boss")
        executed = []

        def say(content: str) -> str:
            executed.append(content)
            return content

        model = MockModel(context_size=10000)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id=k,
                            name="inspect" if k == "A" else "TeamSay",
                            input=json.dumps({"content": k}),
                        )
                        for k in ("A", "B")
                    ],
                    is_last=True,
                ),
                ChatResponse(content=[TextBlock(text="Done")], is_last=True),
            ],
        )
        toolkit = Toolkit(
            tools=[
                FunctionTool(say, name=name) for name in ("inspect", "TeamSay")
            ],
        )
        writing, release = asyncio.Event(), asyncio.Event()
        save = storage.update_session_state

        async def delayed_save(*, state: AgentState, **kwargs: Any) -> None:
            writing.set()
            await release.wait()
            await save(state=state, **kwargs)

        async def snapshot() -> list:
            response = await stream_session_events(
                "leader",
                "boss",
                None,
                ids[0],
                storage,
                bus,
            )
            async with aclosing(response.body_iterator) as stream:
                async for frame in stream:
                    event = json.loads(frame.removeprefix("data: "))
                    if event.get("name") == "subagent_hitl_snapshot":
                        return event["value"]["pending"]
            raise AssertionError("Missing card snapshot")

        with (
            patch.object(
                storage,
                "get_team",
                AsyncMock(return_value=team),
                create=True,
            ),
            patch.object(storage, "update_session_state", delayed_save),
            patch(
                "agentscope.app._service._chat.get_model",
                AsyncMock(return_value=model),
            ),
            patch(
                "agentscope.app._service._chat.get_toolkit",
                AsyncMock(return_value=toolkit),
            ),
        ):
            run = asyncio.create_task(
                service._run_impl(*ids, UserMsg("user", "Start")),
            )
            try:
                await asyncio.wait_for(writing.wait(), 3)
                response = await stream_session_events(
                    "leader",
                    "boss",
                    None,
                    ids[0],
                    storage,
                    bus,
                )
                live = await self.enterAsyncContext(
                    aclosing(response.body_iterator),
                )
                first = json.loads((await anext(live)).removeprefix("data: "))
                self.assertEqual(
                    (first["name"], first["value"]),
                    ("subagent_hitl_snapshot", {"pending": []}),
                )
                self.assertEqual(
                    await service._projection.list(
                        "leader",
                        SubagentHitlProjector.KIND,
                    ),
                    [],
                )
            finally:
                release.set()
                await run
            state = storage.session.state
            calls = state.get_awaiting_tool_calls("a")
            self.assertEqual([c.id for c in calls], ["A", "B"])
            keys = [
                {
                    "worker_session_id": ids[1],
                    "reply_id": state.reply_id,
                    "request_id": state.confirmation_request("a", c.id),
                }
                for c in calls
            ]
            self.assertNotEqual(keys[0]["request_id"], keys[1]["request_id"])
            live_cards = [
                json.loads(
                    (await asyncio.wait_for(anext(live), 2)).removeprefix(
                        "data: ",
                    ),
                )
                for _ in keys
            ]
            self.assertCountEqual(
                [(e["name"], e["value"]["event"]["id"]) for e in live_cards],
                [
                    (SubagentHitlProjector.EVT_REQUIRE, k["request_id"])
                    for k in keys
                ],
            )
            with patch.object(
                AgentState,
                "get_awaiting_tool_calls",
                autospec=True,
                side_effect=AgentState.get_awaiting_tool_calls,
            ) as read_pending:
                self.assertCountEqual(await snapshot(), keys)
                read_pending.assert_called_once()
            answers = [
                UserConfirmResultEvent(
                    reply_id=state.reply_id,
                    confirm_results=[
                        ConfirmResult(
                            tool_call=c,
                            confirmed=c.id == "B",
                            request_id=k["request_id"],
                        ),
                    ],
                )
                for c, k in zip(calls, keys)
            ]
            for answer, duplicate, remaining in (
                (answers[1], False, keys[:1]),
                (answers[1], True, keys[:1]),
                (answers[0], False, []),
            ):
                if duplicate:
                    self.assertIsNone(
                        await SubagentHitlProjector.resolve(
                            service._projection,
                            "leader",
                            state.reply_id,
                            answer,
                        ),
                    )
                else:
                    routed = await chat(
                        ChatRequest(
                            agent_id="boss",
                            session_id="leader",
                            input=answer,
                        ),
                        user_id=ids[0],
                        message_bus=bus,
                        storage=storage,
                    )
                    self.assertEqual(routed.session_id, ids[1])
                    ((_, trigger),) = await bus.queue_drain(
                        MessageBusKeys.wakeup_queue(),
                    )
                    self.assertEqual(
                        (trigger["agent_id"], trigger["input"]),
                        (ids[2], answer.model_dump(mode="json")),
                    )
                await service._run_impl(*ids, answer)
                self.assertEqual(await snapshot(), remaining)
            self.assertEqual(executed, ["B"])

    async def test_leader_recovers_and_answers_missing_member_cards(
        self,
    ) -> None:
        """Recover a form and a confirmation after the first write fails."""
        service, _, storage, bus = self._approval_fixture()
        service._agent_cls = Agent
        service._channel_clients = None
        worker = storage.session
        worker.team_id = "team"
        ids = (worker.user_id, worker.id, worker.agent_id)
        leader = worker.model_copy(
            update={"id": "leader", "agent_id": "boss", "state": AgentState()},
        )
        team = TeamRecord(
            id=worker.team_id,
            user_id=worker.user_id,
            session_id=leader.id,
            leader_agent_id=leader.agent_id,
            data=TeamData(
                name="Test",
                members=[
                    TeamMember(
                        owner_id=worker.user_id,
                        agent_id=worker.agent_id,
                        session_id=worker.id,
                        role="created",
                    ),
                ],
            ),
        )
        question = {
            "header": "Choice",
            "question": "Which?",
            "options": [{"label": k, "description": k} for k in ("A", "B")],
        }

        def write(text: str) -> str:
            return text

        model = MockModel(context_size=10000)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="form",
                            name="AskUser",
                            input=json.dumps({"questions": [question]}),
                        ),
                        ToolCallBlock(
                            id="write",
                            name="write",
                            input='{"text":"draft"}',
                        ),
                    ],
                    is_last=True,
                ),
                ChatResponse(content=[TextBlock(text="Done")], is_last=True),
            ],
        )
        self.enterContext(
            patch.object(
                storage,
                "get_team",
                AsyncMock(return_value=team),
                create=True,
            ),
        )
        self.enterContext(
            patch.object(
                storage,
                "get_session",
                AsyncMock(
                    side_effect=lambda u, a, sid: (
                        leader if sid == leader.id else storage.session
                    ).model_copy(deep=True),
                ),
            ),
        )
        self.enterContext(
            patch(
                "agentscope.app._service._chat.get_model",
                AsyncMock(return_value=model),
            ),
        )
        self.enterContext(
            patch(
                "agentscope.app._service._chat.get_toolkit",
                AsyncMock(
                    return_value=Toolkit(
                        tools=[
                            AskUser(),
                            FunctionTool(write),
                        ],
                    ),
                ),
            ),
        )
        with patch.object(
            service._projection,
            "upsert",
            AsyncMock(side_effect=ConnectionError("projection offline")),
        ) as failed:
            await service._run_impl(*ids, UserMsg("user", "Start"))
        failed.assert_awaited_once()
        projection = service._projection
        projector = service._subagent_hitl
        self.assertEqual(await projection.list(leader.id, projector.KIND), [])
        state = storage.session.state
        response = await stream_session_events(
            leader.id,
            leader.agent_id,
            None,
            worker.user_id,
            storage,
            bus,
        )
        async with aclosing(response.body_iterator) as stream:
            frames = [
                json.loads((await anext(stream)).removeprefix("data: "))
                for _ in range(3)
            ]
        cards = [frame["value"] for frame in frames[:-1]]
        expected = {
            "form": (
                "require_external_execution",
                state.reply_context.tool_approvals["form"].external_request_id,
            ),
            "write": (
                "require_user_confirm",
                state.confirmation_request("a", "write"),
            ),
        }
        self.assertEqual(
            {
                c["event"]["tool_calls"][0]["id"]: (
                    c["event_type"],
                    c["request_id"],
                )
                for c in cards
            },
            expected,
        )
        self.assertEqual(
            frames[-1]["value"],
            {
                "pending": [
                    {
                        "worker_session_id": worker.id,
                        "reply_id": state.reply_id,
                        "request_id": c["request_id"],
                    }
                    for c in cards
                ],
            },
        )
        result = ToolResultBlock(
            id="form",
            name="AskUser",
            output="A",
            state="success",
        )
        for metadata in (
            {},
            {
                "answers": [
                    {"question": "Which?", "selected": ["A"]},
                ],
            },
        ):
            receipt = ExternalExecutionResultEvent(
                reply_id=state.reply_id,
                execution_results=[
                    result.model_copy(
                        update={"metadata": metadata},
                    ),
                ],
            )
            if not metadata:
                with self.assertRaises(ValidationError):
                    ChatRequest(
                        agent_id=leader.agent_id,
                        session_id=leader.id,
                        input=receipt,
                    )
                self.assertEqual(storage.session.state, state)
                continue
            routed = await chat(
                ChatRequest(
                    agent_id=leader.agent_id,
                    session_id=leader.id,
                    input=receipt,
                ),
                user_id=worker.user_id,
                storage=storage,
                message_bus=bus,
            )
            self.assertEqual(routed.session_id, worker.id)
            ((_, trigger),) = await bus.queue_drain(
                MessageBusKeys.wakeup_queue(),
            )
            self.assertEqual(trigger["input"], receipt.model_dump(mode="json"))
            await service._run_impl(*ids, receipt)
            reply = storage.messages[state.reply_id]
            self.assertEqual(
                reply.metadata["external_result_receipts"],
                {"form": receipt.id},
            )
        events = await bus.log_read(MessageBusKeys.session_events(leader.id))
        self.assertEqual(
            [
                e["value"]
                for _, e in events
                if e.get("name") == projector.EVT_RESULT
            ],
            [
                {
                    "worker_session_id": worker.id,
                    "reply_id": state.reply_id,
                    "request_id": expected["form"][1],
                },
            ],
        )
        remaining = await projection.list(leader.id, projector.KIND)
        self.assertEqual(
            [c["request_id"] for c in remaining],
            [expected["write"][1]],
        )
        await projection.purge(leader.id, projector.KIND)
        await projector.sync(
            leader.id,
            worker.id,
            storage.agent,
            storage.session.state,
            [],
            projection,
        )
        self.assertEqual(
            await projection.list(leader.id, projector.KIND),
            remaining,
        )

    async def test_duplicate_external_result_preserves_waiting_reply(
        self,
    ) -> None:
        """Publish only the first outstanding result for each tool."""
        service, _, storage, bus = self._approval_fixture()
        service._channel_clients = None
        service._agent_cls = Agent
        ids = ("user-1", "session-1", "agent-1")
        state = storage.session.state
        first = ToolResultBlock(id="a", name="AskUser", output="first")
        second = ToolResultBlock(
            id="b",
            name="AskUser",
            output="second",
            state="success",
            metadata={"answers": [{"question": "Which?", "selected": ["A"]}]},
        )
        state.append_context(
            "a",
            [
                ToolCallBlock(
                    id="a",
                    name="AskUser",
                    input="{}",
                    state="finished",
                ),
                first,
                ToolCallBlock(
                    id="b",
                    name="AskUser",
                    input="{}",
                    state="submitted",
                ),
            ],
        )
        reply = state.get_reply_message("a").model_copy(deep=True)
        storage.messages[reply.id] = reply
        snapshot = state.model_dump()
        duplicate = ExternalExecutionResultEvent(
            reply_id=reply.id,
            execution_results=[first],
        )
        model = MockModel()
        model.set_responses(
            [
                ChatResponse(content=[TextBlock(text="Done")], is_last=True),
            ],
        )
        with (
            patch(
                "agentscope.app._service._chat.get_model",
                AsyncMock(return_value=model),
            ),
            patch(
                "agentscope.app._service._chat.get_toolkit",
                AsyncMock(return_value=Toolkit()),
            ),
            patch.object(bus, "publish", wraps=bus.publish) as published,
        ):
            await service._run_impl(*ids, duplicate)
            published.assert_not_awaited()
            self.assertEqual(storage.session.state.model_dump(), snapshot)
            self.assertEqual(storage.messages[reply.id], reply)
            batch = duplicate.model_copy(
                update={"execution_results": [first, second, second]},
            )
            await service._run_impl(*ids, batch)
        receipts = [
            payload
            for (_, payload), _ in published.await_args_list
            if payload.get("type") == "EXTERNAL_EXECUTION_RESULT"
        ]
        self.assertEqual(
            [[r["id"] for r in e["execution_results"]] for e in receipts],
            [["b"]],
        )
        self.assertEqual(
            storage.messages[reply.id].finished_reason,
            "completed",
        )
        self.assertEqual(
            storage.session.state.get_awaiting_tool_calls("a"),
            [],
        )

    async def test_stop_after_external_echo_leaves_no_accepted_receipt(
        self,
    ) -> None:
        """Only a consumed result can acknowledge the submitted answer."""
        service, _, storage, bus = self._approval_fixture()
        service._agent_cls = Agent
        service._channel_clients = None
        ids = ("user-1", "session-1", "agent-1")
        state = storage.session.state
        call = ToolCallBlock(
            id="q",
            name="AskUser",
            state="submitted",
            input="{}",
        )
        state.append_context("a", [call])
        reply = state.get_reply_message("a")
        storage.messages[reply.id] = reply.model_copy(deep=True)
        receipt = ExternalExecutionResultEvent(
            reply_id=reply.id,
            execution_results=[
                ToolResultBlock(
                    id=call.id,
                    name=call.name,
                    output="my submitted answer",
                    state="success",
                    metadata={
                        "answers": [{"question": "Q", "selected": ["A"]}],
                    },
                ),
            ],
        )
        publish = bus.publish
        terminals = []

        async def stop_after_echo(channel: str, payload: dict) -> None:
            if payload.get("type") == "EXTERNAL_EXECUTION_RESULT":
                await bus.registry_set(
                    MessageBusKeys.session_interrupts(ids[1]),
                    reply.id,
                    "1",
                )
            if payload.get("type") == "TOOL_RESULT_END":
                terminals.append(
                    (payload["state"], payload.get("external_request_id")),
                )
            await publish(channel, payload)

        with (
            patch(
                "agentscope.app._service._chat.get_model",
                AsyncMock(return_value=MockModel()),
            ),
            patch(
                "agentscope.app._service._chat.get_toolkit",
                AsyncMock(return_value=Toolkit()),
            ),
            patch.object(bus, "publish", stop_after_echo),
        ):
            await service._run_impl(*ids, receipt)
        self.assertEqual(terminals, [("interrupted", None)])
        for message in (
            storage.messages[reply.id],
            storage.session.state.get_reply_message("a"),
        ):
            (result,) = message.get_content_blocks("tool_result")
            self.assertEqual(
                (result.id, result.state, message.metadata),
                ("q", "interrupted", {}),
            )
            self.assertNotIn("my submitted answer", result.model_dump_json())

    async def test_legacy_external_result_recovers_with_original_tool(
        self,
    ) -> None:
        """An old form stays parked until its validator becomes available."""
        service, _, storage, bus = self._approval_fixture()
        service._channel_clients = None
        service._agent_cls = Agent
        ids = ("user-1", "session-1", "agent-1")
        name = "client__choose_option"
        state = storage.session.state
        state.append_context(
            "a",
            [
                ToolCallBlock(
                    id="pick",
                    name=name,
                    state="submitted",
                    input="{}",
                ),
            ],
        )
        reply = state.get_reply_message("a")
        storage.messages[reply.id] = reply.model_copy(deep=True)
        before = state.model_dump()
        submission = ExternalExecutionResultEvent(
            reply_id=reply.id,
            execution_results=[
                ToolResultBlock(
                    id="pick",
                    name=name,
                    output="A",
                    state="success",
                ),
            ],
        )
        model = MockModel()
        model.set_responses(
            [ChatResponse(content=[TextBlock(text="Done")], is_last=True)],
        )
        toolkit = Toolkit()
        with (
            patch(
                "agentscope.app._service._chat.get_model",
                AsyncMock(return_value=model),
            ),
            patch(
                "agentscope.app._service._chat.get_toolkit",
                AsyncMock(return_value=toolkit),
            ),
            patch.object(bus, "publish", wraps=bus.publish) as publish,
        ):
            await service._run_impl(*ids, submission)
            ((args, _),) = publish.await_args_list
            self.assertEqual(
                args[1]["value"],
                {
                    "request_id": submission.id,
                    "reply_id": reply.id,
                    "tool_call_ids": ["pick"],
                    "unavailable_tool": name,
                    "message": f"Re-enable {name!r} or use a client "
                    "configured with the original tool, then submit "
                    "the result again.",
                },
            )
            self.assertEqual(storage.session.state.model_dump(), before)
            await toolkit.add_tool(
                ClientExternalTool(
                    ClientExternalToolDefinition(
                        name=name,
                        description="Pick an option",
                        input_schema={"type": "object", "properties": {}},
                    ),
                ),
            )
            await service._run_impl(*ids, submission)
            publish.reset_mock()
            await service._run_impl(
                *ids,
                submission.model_copy(update={"id": "late"}),
            )
            publish.assert_not_awaited()
        self.assertEqual(
            storage.messages[reply.id].model_dump(exclude_unset=True)[
                "metadata"
            ],
            {"external_result_receipts": {"pick": submission.id}},
        )

    async def test_invalid_external_batch_preserves_form_for_retry(
        self,
    ) -> None:
        """Reject the whole receipt before changing either pending call."""
        service, _, storage, bus = self._approval_fixture()
        service._agent_cls = Agent
        service._channel_clients = None
        ids = ("user-1", "session-1", "agent-1")
        state = storage.session.state
        state.append_context(
            "a",
            [
                ToolCallBlock(
                    id=key,
                    name="AskUser",
                    input="{}",
                    state="submitted",
                )
                for key in ("first", "second")
            ],
        )
        reply = state.get_reply_message("a").model_copy(deep=True)
        storage.messages[reply.id] = reply
        snapshot = state.model_dump()
        answer = ToolResultBlock(
            id="first",
            name="AskUser",
            output="A",
            state="success",
            metadata={"answers": [{"question": "Which?", "selected": ["A"]}]},
        )
        second = answer.model_copy(update={"id": "second"})
        model = MockModel()
        model.set_responses(
            [ChatResponse(content=[TextBlock(text="Done")], is_last=True)],
        )
        with (
            patch(
                "agentscope.app._service._chat.get_model",
                AsyncMock(return_value=model),
            ),
            patch(
                "agentscope.app._service._chat.get_toolkit",
                AsyncMock(return_value=Toolkit()),
            ),
            patch.object(bus, "publish", wraps=bus.publish) as published,
        ):
            for invalid in (
                second.model_copy(update={"metadata": {}}),
                second.model_copy(update={"name": "other"}),
                ToolResultBlock(id="second", name="AskUser", output="A"),
            ):
                receipt = ExternalExecutionResultEvent(
                    reply_id=reply.id,
                    execution_results=[answer, invalid],
                )
                await service._run_impl(*ids, receipt)
                events = [args[1] for args, _ in published.await_args_list]
                self.assertEqual(
                    [
                        (e.get("name"), e["value"]["request_id"])
                        for e in events
                    ],
                    [("external_execution_result_rejected", receipt.id)],
                )
                self.assertEqual(storage.messages[reply.id], reply)
                self.assertEqual(state.model_dump(), snapshot)
                published.reset_mock()
            await service._run_impl(
                *ids,
                ExternalExecutionResultEvent(
                    reply_id=reply.id,
                    execution_results=[answer, second],
                ),
            )
        self.assertEqual(
            storage.messages[reply.id].finished_reason,
            "completed",
        )
        self.assertEqual(
            storage.session.state.get_awaiting_tool_calls("a"),
            [],
        )

    async def test_web_edit_clears_queued_platform_requester(self) -> None:
        """The Web replacement keeps its display name and loses card rights."""
        service, _, storage, bus = self._approval_fixture()
        service._channel_clients = None
        gateway = ChannelGateway(storage, bus, _WorkspaceManager())
        event = ChannelEvent(
            channel_id="chan-1",
            chat_id="chat-1",
            channel_user_id="sender",
            content=[TextBlock(text="Original")],
        )
        agent_id, session_id, _ = resolve(event, storage.channel)
        storage.session.id = session_id
        await gateway.process(event)
        (item,) = await list_chat_inputs(bus, session_id)
        await update_chat_input(
            bus,
            "user-1",
            session_id,
            agent_id,
            item["id"],
            UserMsg(
                name="sender",
                content="Edited",
                metadata={"channel_requester_id": "sender"},
            ),
        )
        queue = ChatInputQueue(bus, storage, service)
        self.addAsyncCleanup(queue.close)
        self.assertTrue(
            await queue.drain_chat_inputs("user-1", session_id, agent_id),
        )
        self.assertEqual(
            [
                m.get_text_content()
                for m in storage.messages.values()
                if m.role == "user"
            ],
            ["Edited"],
        )
        self.assertTrue(storage.session.state.has_awaiting_tool_calls("a"))
        self.assertEqual(
            await bus.registry_getall(
                MessageBusKeys.channel_session_requesters(session_id),
            ),
            {},
        )

    async def test_previous_external_result_leaves_current_card_open(
        self,
    ) -> None:
        """An old result cannot use a stale stop marker against this reply."""
        service, _, storage, bus = self._approval_fixture()
        service._channel_clients = None
        ids = ("user-1", "session-1", "agent-1")
        await service._run_impl(*ids, UserMsg("requester", "Start"))
        state = storage.session.state
        (call,) = state.get_awaiting_tool_calls("a")
        snapshot = state.model_dump()
        await bus.registry_set(
            MessageBusKeys.session_interrupts(ids[1]),
            "previous",
            "1",
        )
        late = ExternalExecutionResultEvent(
            reply_id="previous",
            execution_results=[
                ToolResultBlock(id=call.id, name=call.name, output="old"),
            ],
        )
        with patch.object(bus, "publish", wraps=bus.publish) as published:
            await service._run_impl(*ids, late)
            published.assert_not_awaited()
        self.assertEqual(storage.session.state.model_dump(), snapshot)
        await service._run_impl(
            *ids,
            UserConfirmResultEvent(
                reply_id=state.reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=True,
                        tool_call=call,
                        request_id=state.confirmation_request("a", call.id),
                    ),
                ],
            ),
        )
        self.assertEqual(
            [c.id for c in storage.session.state.get_awaiting_tool_calls("a")],
            ["tool-2"],
        )

    async def test_stop_while_confirmation_environment_is_loading(
        self,
    ) -> None:
        """Stop wins before tool execution even if pub/sub is delayed."""
        for after_receipt in (False, True):
            for deliver_signal in (False, True):
                with self.subTest(
                    after_receipt=after_receipt,
                    signal=deliver_signal,
                ):
                    await self._stop_loading_confirmation(
                        deliver_signal,
                        after_receipt,
                    )

    async def _stop_loading_confirmation(
        self,
        deliver_signal: bool,
        after_receipt: bool,
    ) -> None:
        service, _, storage, bus = self._approval_fixture()
        service._channel_clients = None
        ids = ("user-1", "session-1", "agent-1")
        await service._run_impl(*ids, UserMsg("requester", "Start"))
        state = storage.session.state
        (call,) = state.get_awaiting_tool_calls("a")
        original = '{"value":"original"}'
        edited = '{"value":"edited"}'
        call.input = original
        replay = state.get_reply_message("a").model_copy(deep=True)
        storage.messages[state.reply_id] = replay.model_copy(deep=True)
        receipt = UserConfirmResultEvent(
            reply_id=state.reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=True,
                    tool_call=call.model_copy(update={"input": edited}),
                    request_id=state.confirmation_request("a", call.id),
                ),
            ],
        )
        executed = []

        def edit(value: str) -> str:
            executed.append(value)
            return "done"

        tool = FunctionTool(edit, name=call.name)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def load(*_: object) -> MockModel:
            if not after_receipt:
                entered.set()
                await release.wait()
            return MockModel()

        signals = []
        receipts = []
        publish = bus.publish

        async def dispatch(channel: str, payload: dict) -> None:
            if channel == MessageBusKeys.session_events(ids[1]):
                event = _EVENT_ADAPTER.validate_python(payload)
                replay.append_event(event)
                if isinstance(event, UserConfirmResultEvent):
                    receipts.append(event.id)
                    if after_receipt:
                        entered.set()
                        await release.wait()
            if channel == MessageBusKeys.session_interrupt_channel():
                signals.append(payload)
                if deliver_signal:
                    run.cancel()
            await publish(channel, payload)

        service._agent_cls = Agent
        with (
            patch("agentscope.app._service._chat.get_model", load),
            patch(
                "agentscope.app._service._chat.get_toolkit",
                AsyncMock(return_value=Toolkit(tools=[tool])),
            ),
            patch.object(bus, "publish", dispatch),
        ):
            run = asyncio.create_task(service._run_impl(*ids, receipt))
            try:
                await asyncio.wait_for(entered.wait(), timeout=2)
                await service.interrupt(*ids)
                if after_receipt and deliver_signal:
                    # Let the first cancellation reach the shielded wait.
                    await asyncio.sleep(0)
                    await service.interrupt(*ids)
                    await asyncio.sleep(0)
            finally:
                release.set()
                await asyncio.gather(run, return_exceptions=True)
            if not deliver_signal:
                self.assertEqual(
                    storage.messages[state.reply_id].finished_reason,
                    ReplyFinishedReason.INTERRUPTED,
                )
            stops = [
                UserInterruptEvent.model_validate(trigger["input"])
                for _, trigger in await bus.queue_drain(
                    MessageBusKeys.wakeup_queue(),
                )
            ]
            for stop in stops:
                await service._run_impl(*ids, stop)
        stop_count = 2 if after_receipt and deliver_signal else 1
        expected_input = edited if after_receipt else original
        self.assertEqual(
            (
                receipts,
                [
                    message.get_content_blocks("tool_call")[0].input
                    for message in (
                        replay,
                        storage.messages[state.reply_id],
                        storage.session.state.get_reply_message("a"),
                    )
                ],
            ),
            ([receipt.id] if after_receipt else [], [expected_input] * 3),
        )
        self.assertEqual(
            (
                executed,
                signals,
                [stop.reply_id for stop in stops],
                storage.messages[state.reply_id].finished_reason,
                storage.session.state.has_awaiting_tool_calls("a"),
                await bus.registry_getall(
                    MessageBusKeys.session_interrupts(ids[1]),
                ),
            ),
            (
                [],
                [{"session_id": ids[1]}] * stop_count,
                [state.reply_id] * stop_count,
                ReplyFinishedReason.INTERRUPTED,
                False,
                {},
            ),
        )

    async def test_delayed_feishu_delivery_keeps_requester_after_web_approval(
        self,
    ) -> None:
        """An early Web decision must preserve the next card's buttons."""
        for during_rename in (True, False):
            with self.subTest(during_rename=during_rename):
                await self._verify_channel_card(during_rename)

    async def _verify_channel_card(self, during_rename: bool) -> None:
        """Retain the sender during a rename and at the end of a run."""
        service, clients, storage, bus = self._approval_fixture()
        sending = asyncio.Event()
        release_send = asyncio.Event()

        async def delayed_card_open(_: ChannelEvent) -> str:
            if not sending.is_set():
                sending.set()
                await release_send.wait()
            return "stream-card"

        self.enterContext(
            patch.object(
                FeishuChannel,
                "_card_open",
                new=AsyncMock(side_effect=delayed_card_open),
            ),
        )
        send = self.enterContext(
            patch.object(FeishuChannel, "_send", new_callable=AsyncMock),
        )

        async def run(inputs: Msg | UserConfirmResultEvent) -> None:
            await asyncio.wait_for(
                service._run_impl("user-1", session_id, "agent-1", inputs),
                timeout=2,
            )

        event = ChannelEvent(
            channel_id="chan-1",
            chat_id="chat-1",
            channel_user_id="requester-1",
            content=[TextBlock(text="Run both tools")],
        )
        agent_id, session_id, _ = resolve(event, storage.channel)
        storage.session.id = session_id
        gateway = ChannelGateway(storage, bus, _WorkspaceManager())

        async def save_during_message(**kwargs: Any) -> SessionRecord:
            await gateway.process(event)
            storage.session.config = kwargs["config"]
            return storage.session

        if during_rename:
            with patch.object(
                storage,
                "upsert_session",
                save_during_message,
                create=True,
            ):
                await update_session(
                    session_id,
                    UpdateSessionRequest(name="renamed"),
                    agent_id,
                    "user-1",
                    storage,
                    service._access,
                    bus,
                )
            self.assertEqual(storage.session.config.name, "renamed")
        else:
            state = storage.session.state
            state.reply_id = "previous"
            prior = ToolCallBlock(
                id="previous-tool",
                name="old",
                input="{}",
                state="asking",
            )
            state.append_context("a", [prior])
            message = state.get_reply_message("a")
            message.set_confirmation_request(prior.id, "previous-card")
            storage.messages[state.reply_id] = message.model_copy(deep=True)
            async with session_execution(bus, session_id):
                await register_inbox_consumer(bus, session_id)
                self.assertFalse(
                    await has_pending_inbox_or_release(bus, session_id),
                )
                await gateway.process(event)
        ((_, trigger),) = await bus.queue_drain(MessageBusKeys.wakeup_queue())
        self.assertEqual(trigger["kind"], "queued_message")
        queue = ChatInputQueue(bus, storage, service)
        self.addAsyncCleanup(queue.close)
        ids = ("user-1", session_id, agent_id)
        (queued,) = await list_chat_inputs(bus, session_id)
        incoming = Msg.model_validate(queued["input"])
        if not during_rename:
            self.assertFalse(await queue.drain_chat_inputs(*ids))
            self.assertEqual(list(storage.messages), ["previous"])
            self.assertEqual(await list_chat_inputs(bus, session_id), [queued])
            with patch.object(service, "_channel_clients", None):
                await run(
                    UserConfirmResultEvent(
                        reply_id="previous",
                        confirm_results=[
                            ConfirmResult(
                                confirmed=True,
                                tool_call=prior,
                                request_id="previous-card",
                            ),
                        ],
                    ),
                )
            self.assertEqual(
                storage.messages["previous"].finished_reason,
                ReplyFinishedReason.COMPLETED,
            )
        self.assertTrue(await queue.drain_chat_inputs(*ids))
        self.assertEqual(await list_chat_inputs(bus, session_id), [])
        self.assertEqual(
            [m for m in storage.session.state.context if m.role == "user"],
            [incoming],
        )
        self.assertEqual(storage.messages[incoming.id], incoming)
        await asyncio.wait_for(sending.wait(), timeout=2)
        first_deliveries = set(clients._deliveries)
        self.assertFalse(release_send.is_set())
        self.assertFalse(
            await bus.is_locked(MessageBusKeys.session_lock(session_id)),
        )
        self.assertDictEqual(
            await bus.registry_getall(
                MessageBusKeys.channel_session_approvals(session_id),
            ),
            {},
        )

        # Submit the Web decision before any channel approval card exists.
        pending = storage.session.state.get_awaiting_tool_calls("a")
        await run(
            UserConfirmResultEvent(
                reply_id="r-1",
                confirm_results=[
                    ConfirmResult(
                        request_id=storage.session.state.confirmation_request(
                            "a",
                            pending[0].id,
                        ),
                        confirmed=True,
                        tool_call=pending[0],
                    ),
                ],
            ),
        )
        await asyncio.wait_for(
            asyncio.gather(*(clients._deliveries - first_deliveries)),
            timeout=2,
        )
        self.assertEqual(send.await_count, 1)
        self.assertEqual(send.await_args.args[2], "interactive")
        card = json.loads(send.await_args.args[3])
        actions = card["elements"][-1]["actions"]
        approval_id = actions[0]["value"]["approval_id"]
        record = await load_approval(bus, approval_id)
        pending = storage.session.state.get_awaiting_tool_calls("a")
        self.assertTrue(
            storage.session.state.confirmation_request("a", pending[0].id),
        )
        self.assertDictEqual(
            record.model_dump(),
            {
                "channel_id": "chan-1",
                "chat_id": "chat-1",
                "agent_id": "agent-1",
                "session_id": session_id,
                "reply_id": "r-1",
                "tool_call_id": "tool-2",
                "confirmation_id": storage.session.state.confirmation_request(
                    "a",
                    pending[0].id,
                ),
                "requester_id": "requester-1",
            },
        )
        self.assertListEqual(
            [action["value"]["action"] for action in actions],
            ["approve", "deny"],
        )

        await run(
            UserConfirmResultEvent(
                reply_id="r-1",
                confirm_results=[
                    ConfirmResult(
                        request_id=storage.session.state.confirmation_request(
                            "a",
                            pending[0].id,
                        ),
                        confirmed=True,
                        tool_call=pending[0],
                    ),
                ],
            ),
        )
        # A late first delivery must not resurrect completed requester state.
        release_send.set()
        await asyncio.wait_for(asyncio.gather(*clients._deliveries), timeout=2)
        self.assertDictEqual(
            {
                kind: await bus.registry_getall(namespace(session_id))
                for kind, namespace in (
                    ("requesters", MessageBusKeys.channel_session_requesters),
                    ("approvals", MessageBusKeys.channel_session_approvals),
                )
            },
            {"requesters": {}, "approvals": {}},
        )

    async def test_paused_requester_is_saved_under_lock_without_delivery(
        self,
    ) -> None:
        """Both pause kinds persist identity even without a channel runtime."""
        for pause_event in (
            RequireUserConfirmEvent,
            RequireExternalExecutionEvent,
        ):
            with self.subTest(pause_event=pause_event.__name__):
                await self._assert_paused_requester_saved(pause_event)

    async def _assert_paused_requester_saved(
        self,
        pause_event: type[
            RequireUserConfirmEvent | RequireExternalExecutionEvent
        ],
    ) -> None:
        """Observe identity persistence while the session is locked."""
        service, _, storage, bus = self._approval_fixture()
        service._channel_clients = None
        namespace = MessageBusKeys.channel_session_requesters(
            "session-1",
        )
        observed = []
        registry_set = bus.registry_set

        async def observe_write(
            name: str,
            field: str,
            value: str,
            *,
            ttl_secs: int | None = None,
        ) -> None:
            if name == namespace:
                state = storage.session.state
                observed.append(
                    {
                        "locked": await bus.is_locked(
                            MessageBusKeys.session_lock("session-1"),
                        ),
                        "pending": [
                            tool.id
                            for tool in state.get_awaiting_tool_calls("a")
                        ],
                        "requester": {field: value},
                    },
                )
            await registry_set(name, field, value, ttl_secs=ttl_secs)

        with (
            patch.object(_TwoApprovalAgent, "pause_event", pause_event),
            patch.object(bus, "registry_set", side_effect=observe_write),
        ):
            await service._run_impl(
                "user-1",
                "session-1",
                "agent-1",
                UserMsg(name="requester-1", content="Pause"),
                channel_requester_id="requester-1",
            )
        self.assertListEqual(
            observed,
            [
                {
                    "locked": True,
                    "pending": ["tool-1"],
                    "requester": {"r-1": "requester-1"},
                },
            ],
        )
        self.assertDictEqual(
            await bus.registry_getall(namespace),
            {"r-1": "requester-1"},
        )

    async def test_identity_free_pause_does_not_use_session_origin_user(
        self,
    ) -> None:
        """Web and unverifiable channel inputs remain fail-closed."""
        for message in (
            UserMsg(name="user", content="Web request"),
            UserMsg(
                name="unverified-user",
                content="Channel request without a trusted user id",
                metadata={CHANNEL_APPROVAL_UNAVAILABLE: True},
            ),
        ):
            with self.subTest(message=message.name):
                service, _, storage, bus = self._approval_fixture()
                service._channel_clients = None
                storage.session.origin.channel_user_id = "another-user"
                await service._run_impl(
                    "user-1",
                    "session-1",
                    "agent-1",
                    message,
                )
                self.assertTrue(
                    storage.session.state.has_awaiting_tool_calls("a"),
                )
                self.assertDictEqual(
                    await bus.registry_getall(
                        MessageBusKeys.channel_session_requesters("session-1"),
                    ),
                    {},
                )

    async def test_cancellation_waits_for_requester_persistence(
        self,
    ) -> None:
        """Cancellation cannot release the lock before identity is saved."""
        service, _, storage, bus = self._approval_fixture()
        service._channel_clients = None
        namespace = MessageBusKeys.channel_session_requesters("session-1")
        writing = asyncio.Event()
        finish_write = asyncio.Event()
        registry_set = bus.registry_set

        async def delayed_write(
            name: str,
            field: str,
            value: str,
            *,
            ttl_secs: int | None = None,
        ) -> None:
            if name == namespace:
                writing.set()
                await finish_write.wait()
            await registry_set(name, field, value, ttl_secs=ttl_secs)

        with patch.object(bus, "registry_set", side_effect=delayed_write):
            run = asyncio.create_task(
                service._run_impl(
                    "user-1",
                    "session-1",
                    "agent-1",
                    UserMsg(name="requester-1", content="Pause"),
                    channel_requester_id="requester-1",
                ),
            )
            try:
                await asyncio.wait_for(writing.wait(), timeout=2)
                run.cancel()
                await asyncio.sleep(0)
                self.assertFalse(run.done())
                self.assertTrue(
                    await bus.is_locked(
                        MessageBusKeys.session_lock("session-1"),
                    ),
                )
            finally:
                finish_write.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(run, timeout=2)
        self.assertDictEqual(
            {
                "requesters": await bus.registry_getall(namespace),
                "locked": await bus.is_locked(
                    MessageBusKeys.session_lock("session-1"),
                ),
                "pending": [
                    tool.id
                    for tool in storage.session.state.get_awaiting_tool_calls(
                        "a",
                    )
                ],
            },
            {
                "requesters": {"r-1": "requester-1"},
                "locked": False,
                "pending": ["tool-1"],
            },
        )

    async def _run(
        self,
        source: SessionOrigin,
        *,
        channel_enabled: bool = True,
    ) -> ChannelClients:
        """Drive one run to completion and return the channel runtime."""
        user_id, agent, session, channel = self._fixture(source)
        channel.enabled = channel_enabled
        storage = _Storage(session, agent, channel)
        bus = InMemoryMessageBus()
        if isinstance(source, ChannelOrigin):
            await remember_reply_requester(
                bus,
                session_id=session.id,
                reply_id="r-1",
                requester_id="u",
            )
        clients = ChannelClients(
            storage=storage,
            message_bus=bus,
            type_registry=ChannelTypeRegistry([_RecordingChannel]),
        )

        class _Agent:
            """Reply with nothing; the run's own events are enough."""

            def __init__(self, *, state: object = None, **_: object) -> None:
                self.state = state

            async def reply_stream(
                self,
                inputs: object,
            ) -> AsyncGenerator[object, None]:
                """Emit a minimal, well-formed reply."""
                del inputs
                yield ReplyStartEvent(
                    reply_id="r-1",
                    session_id="session-1",
                    name="a",
                )
                yield ReplyEndEvent(
                    reply_id="r-1",
                    session_id="session-1",
                    name="a",
                    finished_reason=ReplyFinishedReason.COMPLETED,
                )

        async def _get_toolkit(**_: object) -> object:
            return object()

        async def _get_model(*_: object, **__: object) -> object:
            return object()

        class _Access:
            """Resolve the run's own agent."""

            async def resolve_agent(self, *_: object) -> AgentRecord:
                """Return a detached copy."""
                return agent.model_copy(deep=True)

        service = ChatService(
            storage=storage,
            workspace_manager=_WorkspaceManager(),
            scheduler_manager=object(),
            background_task_manager=object(),
            message_bus=bus,
            resource_access_service=_Access(),
            custom_agent_cls=_Agent,
            channel_clients=clients,
        )
        with (
            patch(
                "agentscope.app._service._chat.get_toolkit",
                new=_get_toolkit,
            ),
            patch("agentscope.app._service._chat.get_model", new=_get_model),
        ):
            await service._run_impl(
                user_id,
                session.id,
                agent.id,
                UserMsg(name="u", content=[TextBlock(text="hi")]),
            )
        return clients

    async def test_the_reply_reaches_the_chat_the_session_came_from(
        self,
    ) -> None:
        """The run finishes before the channel starts reading, so the
        delivery has to replay the log rather than miss the reply."""
        clients = await self._run(
            ChannelOrigin(channel_id="chan-1", chat_id="chat-1"),
        )
        try:
            self.assertEqual(len(_RecordingChannel.instances), 1)
            channel = _RecordingChannel.instances[0]
            await asyncio.wait_for(channel.done.wait(), timeout=2.0)
            self.assertEqual(
                await load_reply_requester(
                    clients._bus,  # pylint: disable=protected-access
                    session_id="session-1",
                    reply_id="r-1",
                ),
                "",
            )

            assert channel.target is not None
            self.assertDictEqual(
                {
                    "chat_id": channel.target.chat_id,
                    "channel_id": channel.target.channel_id,
                    "metadata": channel.target.metadata,
                    "events": channel.seen,
                },
                {
                    "chat_id": "chat-1",
                    "channel_id": "chan-1",
                    "metadata": {
                        "session_id": "session-1",
                        "agent_id": "agent-1",
                    },
                    "events": ["REPLY_START", "REPLY_END"],
                },
            )
        finally:
            await clients.__aexit__(None, None, None)

    async def test_completed_reply_cleans_requester_without_delivery(
        self,
    ) -> None:
        """Clean completed requesters even when the channel is disabled."""
        clients = await self._run(
            ChannelOrigin(channel_id="chan-1", chat_id="chat-1"),
            channel_enabled=False,
        )
        try:
            self.assertListEqual(_RecordingChannel.instances, [])
            self.assertEqual(
                await load_reply_requester(
                    clients._bus,
                    session_id="session-1",
                    reply_id="r-1",
                ),
                "",
            )
        finally:
            await clients.__aexit__(None, None, None)

    async def test_a_web_session_delivers_nothing(self) -> None:
        """Only a channel-originated run has a chat to reply into."""
        clients = await self._run(UserOrigin())
        try:
            self.assertListEqual(_RecordingChannel.instances, [])
        finally:
            await clients.__aexit__(None, None, None)

    async def test_a_private_chat_equips_tools_with_its_user(self) -> None:
        """A 1:1 session has one sender, so tools may act as them."""
        clients = await self._run(
            ChannelOrigin(
                channel_id="chan-1",
                chat_id="private:chat-1",
                channel_user_id="staff-1",
            ),
        )
        try:
            self.assertListEqual(_RecordingChannel.tool_user_ids, ["staff-1"])
        finally:
            await clients.__aexit__(None, None, None)

    async def test_a_group_chat_equips_tools_with_no_user(self) -> None:
        """A shared session must not let tools act as one member."""
        clients = await self._run(
            ChannelOrigin(
                channel_id="chan-1",
                chat_id="group:chat-1",
                channel_user_id="staff-1",
            ),
        )
        try:
            self.assertListEqual(_RecordingChannel.tool_user_ids, [None])
        finally:
            await clients.__aexit__(None, None, None)

    async def test_an_unclassifiable_chat_equips_no_user(self) -> None:
        """An audience the platform cannot classify is treated as shared."""
        clients = await self._run(
            ChannelOrigin(
                channel_id="chan-1",
                chat_id="chat-1",
                channel_user_id="staff-1",
            ),
        )
        try:
            self.assertListEqual(_RecordingChannel.tool_user_ids, [None])
        finally:
            await clients.__aexit__(None, None, None)
