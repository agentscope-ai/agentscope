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
from types import SimpleNamespace
from typing import Any, AsyncGenerator, AsyncIterator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from pydantic import BaseModel

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import ChatService
from agentscope.app.channel import (
    ChannelBase,
    ChannelClients,
    ChannelEvent,
    ChannelStatus,
    ChannelTypeRegistry,
)
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.event import ReplyEndEvent, ReplyStartEvent
from agentscope.types import ReplyFinishedReason
from agentscope.message import TextBlock, UserMsg
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
    SessionSource,
)


class _RecordingChannel(ChannelBase):
    """Captures the send target and the events it was fed."""

    channel_type = "fake"
    display_name = "Fake"
    platform_bot_id_field = "bot_id"
    instances: list["_RecordingChannel"] = []

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

    async def get_session(self, *_: object, **__: object) -> SessionRecord:
        """Return a detached copy."""
        return self.session.model_copy(deep=True)

    async def get_agent(self, *_: object, **__: object) -> AgentRecord:
        """Return a detached copy."""
        return self.agent.model_copy(deep=True)

    async def get_channel(self, *_: object, **__: object) -> ChannelRecord:
        """Return the one channel this stub serves."""
        return self.channel

    async def update_session_state(self, *_: object, **__: object) -> None:
        """Accept the post-run state persistence."""

    async def upsert_message(self, *_: object, **__: object) -> None:
        """Accept persisted reply messages."""


class _WorkspaceManager:
    """Return a minimal workspace handle."""

    async def get_workspace(self, *_: object, **__: object) -> object:
        """Return an inert workspace."""
        return SimpleNamespace(workdir="/tmp/agentscope-delivery-test")


class ChannelDeliveryFromTheRunTest(IsolatedAsyncioTestCase):
    """The run starts the delivery; the channel runtime owns it."""

    def setUp(self) -> None:
        """Isolate the instances each test observes."""
        _RecordingChannel.instances.clear()

    def _fixture(self, source: SessionSource) -> tuple:
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
            source=source,
            source_channel_id=(
                "chan-1" if source is SessionSource.CHANNEL else None
            ),
            source_chat_id=(
                "chat-1" if source is SessionSource.CHANNEL else None
            ),
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
            created_at="t",
            updated_at="t",
        )
        return user_id, agent, session, channel

    async def _run(self, source: SessionSource) -> ChannelClients:
        """Drive one run to completion and return the channel runtime."""
        user_id, agent, session, channel = self._fixture(source)
        storage = _Storage(session, agent, channel)
        bus = InMemoryMessageBus()
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
        clients = await self._run(SessionSource.CHANNEL)
        try:
            self.assertEqual(len(_RecordingChannel.instances), 1)
            channel = _RecordingChannel.instances[0]
            await asyncio.wait_for(channel.done.wait(), timeout=2.0)

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

    async def test_a_web_session_delivers_nothing(self) -> None:
        """Only a channel-originated run has a chat to reply into."""
        clients = await self._run(SessionSource.USER)
        try:
            self.assertListEqual(_RecordingChannel.instances, [])
        finally:
            await clients.__aexit__(None, None, None)
