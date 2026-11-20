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
from types import SimpleNamespace
from typing import Any, AsyncGenerator, AsyncIterator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import ChatService
from agentscope.app.channel import (
    ChannelBase,
    ChannelClients,
    ChannelEvent,
    ChannelStatus,
    ChannelTypeRegistry,
    ChatKind,
    FeishuChannel,
    SlackChannel,
)
from agentscope.app.channel._approval import (
    CHANNEL_APPROVAL_UNAVAILABLE,
    load_approval,
    load_reply_requester,
    remember_reply_requester,
)
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.event import (
    ConfirmResult,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireUserConfirmEvent,
    RequireExternalExecutionEvent,
    TextBlockStartEvent,
    TextBlockDeltaEvent,
    UserConfirmResultEvent,
)
from agentscope.types import ReplyFinishedReason
from agentscope.message import (
    AssistantMsg,
    Msg,
    TextBlock,
    ToolCallBlock,
    UserMsg,
)
from agentscope.state import AgentState
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


class _SlackWeb:
    """Answer the one Web API lookup a Slack run makes before its tools."""

    def __init__(self, **_: object) -> None:
        """Accept the client's constructor arguments."""

    async def conversations_info(self, channel: str) -> dict:
        """Classify ``D...`` ids as direct messages, the rest as groups."""
        return {"channel": {"is_im": channel.startswith("D")}}


class _WorkspaceManager:
    """Return a minimal workspace handle."""

    async def get_workspace(self, *_: object, **__: object) -> object:
        """Return an inert workspace."""
        return SimpleNamespace(
            workdir="/tmp/agentscope-delivery-test",
            get_backend=lambda: None,
        )


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
        self.state.reply_id = "r-1"
        if isinstance(inputs, Msg):
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
                reply_id="r-1",
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
        self.state.context = [
            AssistantMsg(id="r-1", name=self.name, content=[tool]),
        ]
        yield self.pause_event(reply_id="r-1", tool_calls=[tool])


class ChannelDeliveryFromTheRunTest(IsolatedAsyncioTestCase):
    """The run starts the delivery; the channel runtime owns it."""

    def setUp(self) -> None:
        """Isolate the instances each test observes."""
        _RecordingChannel.instances.clear()
        _RecordingChannel.tool_user_ids.clear()
        self.channel_tools: list = []

    def _fixture(
        self,
        source: SessionOrigin,
        channel_type: str = "fake",
        credentials: dict | None = None,
    ) -> tuple:
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
            channel_type=channel_type,
            user_id=user_id,
            credentials=credentials or {"bot_id": "bot-1"},
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

    async def test_delayed_feishu_delivery_keeps_requester_after_web_approval(
        self,
    ) -> None:
        """An early Web decision must preserve the next card's buttons."""
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
                service._run_impl("user-1", "session-1", "agent-1", inputs),
                timeout=2,
            )

        await run(UserMsg(name="requester-1", content="Run both tools"))
        await asyncio.wait_for(sending.wait(), timeout=2)
        first_deliveries = set(clients._deliveries)
        self.assertFalse(release_send.is_set())
        self.assertFalse(
            await bus.is_locked(MessageBusKeys.session_lock("session-1")),
        )
        self.assertDictEqual(
            await bus.registry_getall(
                MessageBusKeys.channel_session_approvals("session-1"),
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
        self.assertDictEqual(
            record.model_dump(),
            {
                "channel_id": "chan-1",
                "chat_id": "chat-1",
                "agent_id": "agent-1",
                "session_id": "session-1",
                "reply_id": "r-1",
                "tool_call_id": "tool-2",
                "requester_id": "requester-1",
            },
        )
        self.assertListEqual(
            [action["value"]["action"] for action in actions],
            ["approve", "deny"],
        )

        pending = storage.session.state.get_awaiting_tool_calls("a")
        await run(
            UserConfirmResultEvent(
                reply_id="r-1",
                confirm_results=[
                    ConfirmResult(
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
                kind: await bus.registry_getall(namespace("session-1"))
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
        channel_cls: type[ChannelBase] = _RecordingChannel,
        credentials: dict | None = None,
        *,
        channel_enabled: bool = True,
    ) -> ChannelClients:
        """Drive one run to completion and return the channel runtime."""
        user_id, agent, session, channel = self._fixture(
            source,
            channel_cls.channel_type,
            credentials,
        )
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
            type_registry=ChannelTypeRegistry([channel_cls]),
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

        async def _get_toolkit(**kwargs: Any) -> object:
            self.channel_tools = kwargs.get("channel_tools") or []
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

    async def _run_slack(self, chat_id: str) -> Any:
        """Drive a Slack-bound run and return the spy on its tool lookup."""
        with (
            patch("slack_sdk.web.async_client.AsyncWebClient", _SlackWeb),
            patch.object(
                SlackChannel,
                "list_tools",
                autospec=True,
                side_effect=SlackChannel.list_tools,
            ) as list_tools,
        ):
            clients = await self._run(
                ChannelOrigin(
                    channel_id="chan-1",
                    chat_id=chat_id,
                    channel_user_id="U1",
                ),
                SlackChannel,
                {"app_id": "A1", "bot_token": "xoxb-x", "app_token": "xapp-x"},
            )
        await clients.__aexit__(None, None, None)
        return list_tools

    async def test_a_slack_private_chat_gets_the_slack_tools(self) -> None:
        """ChatService passes the DM's user through to the Slack tools."""
        list_tools = await self._run_slack("D1")
        self.assertEqual(list_tools.call_args.args[2], "U1")
        self.assertListEqual(
            [tool.name for tool in self.channel_tools],
            [
                "ListChats",
                "ListChatMembers",
                "SendMessage",
                "SendFile",
                "SendImage",
            ],
        )

    async def test_a_slack_group_chat_gets_the_slack_tools(self) -> None:
        """A shared Slack channel gets the tools but no acting user."""
        list_tools = await self._run_slack("C1")
        self.assertIsNone(list_tools.call_args.args[2])
        self.assertEqual(len(self.channel_tools), 5)
