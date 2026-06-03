# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""End-to-end integration tests for the team feature.

Wires up real RedisStorage, RedisMessageBus, WakeupDispatcher and
TeamService against fakeredis. Only ChatService is faked — it just
records every ``run()`` call and signals an event so we can wait
deterministically — that's enough to verify the whole
``TeamService → bus → dispatcher → chat_service.run`` pipeline.
"""
import asyncio
from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._manager import WakeupDispatcher
from agentscope.app import MessageBus
from agentscope.app._service import TeamService
from agentscope.app import RedisMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    RedisKeyConfig,
    RedisStorage,
    SessionConfig,
)


def _make_storage(client) -> RedisStorage:
    """RedisStorage backed by a shared fakeredis client."""
    s = RedisStorage.__new__(RedisStorage)
    s._client = client
    s.key_ttl = None
    s.key_config = RedisKeyConfig()
    return s


def _make_bus(client) -> RedisMessageBus:
    """RedisMessageBus backed by the same fakeredis client."""
    bus = RedisMessageBus.__new__(RedisMessageBus)
    bus._client = client
    bus._owned_pool = None
    return bus


class _RecordingChatService:
    """Stand-in ChatService that records every run() call.

    Each call also resolves a per-session asyncio.Event so tests can
    deterministically wait for the dispatcher to wake a session.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.events: dict[str, asyncio.Event] = {}

    async def run(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg=None,
    ) -> None:
        self.calls.append(
            {
                "user_id": user_id,
                "session_id": session_id,
                "agent_id": agent_id,
                "input_msg": input_msg,
            },
        )
        self.events.setdefault(session_id, asyncio.Event()).set()

    def event_for(self, session_id: str) -> asyncio.Event:
        return self.events.setdefault(session_id, asyncio.Event())

    def calls_for(self, session_id: str) -> list[dict]:
        return [c for c in self.calls if c["session_id"] == session_id]


def _make_leader_agent(user_id: str) -> AgentRecord:
    """Build a regular user-created leader agent."""
    return AgentRecord(
        user_id=user_id,
        data=AgentData(
            name="leader-agent",
            system_prompt="lead.",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


def _make_session_config() -> SessionConfig:
    """Session config with a chat model so worker inheritance works."""
    return SessionConfig(
        workspace_id="ws-1",
        chat_model_config=ChatModelConfig(
            type="openai",
            credential_id="cred-1",
            model="gpt-4",
            parameters={},
        ),
    )


class TestTeamE2E(IsolatedAsyncioTestCase):
    """Integration tests across TeamService + bus + WakeupDispatcher."""

    async def asyncSetUp(self) -> None:
        """Spin up storage + bus + dispatcher + team service against
        a single shared fakeredis instance."""
        self.user_id = "user-1"
        self._fr = fakeredis.aioredis.FakeRedis(decode_responses=True)
        self.storage = _make_storage(self._fr)
        self.bus = _make_bus(self._fr)

        self.fake_chat = _RecordingChatService()

        self.dispatcher = WakeupDispatcher(
            message_bus=self.bus,
            chat_service=self.fake_chat,  # type: ignore[arg-type]
        )
        await self.dispatcher.start()

        self.team_service = TeamService(
            storage=self.storage,
            message_bus=self.bus,
        )

        # Build a leader agent + session.
        self.leader = _make_leader_agent(self.user_id)
        await self.storage.upsert_agent(self.user_id, self.leader)
        leader_session = await self.storage.upsert_session(
            self.user_id,
            self.leader.id,
            _make_session_config(),
        )
        self.leader_session_id = leader_session.id

    async def asyncTearDown(self) -> None:
        """Stop the dispatcher and close fakeredis."""
        await self.dispatcher.stop()
        await self._fr.aclose()

    async def _settle(self, ticks: int = 8) -> None:
        """Yield to the loop a few times so pub/sub frames flush
        and spawned chat tasks have a chance to record their calls."""
        for _ in range(ticks):
            await asyncio.sleep(0)

    async def _create_team(self) -> str:
        """Create a team led by the fixture leader. Returns team_id."""
        team = await self.team_service.create_team(
            user_id=self.user_id,
            leader_session_id=self.leader_session_id,
            leader_agent_id=self.leader.id,
            name="t",
            description="research",
        )
        return team.id

    # ------------------------------------------------------------------

    async def test_add_member_seeds_inbox_and_wakes_worker(self) -> None:
        """Spawning a worker pushes its initial task to the inbox AND
        the dispatcher fires chat_service.run for the worker."""
        team_id = await self._create_team()

        worker = await self.team_service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="alice",
            description="researcher",
            prompt="find facts about ducks",
            permission_mode="default",
        )
        worker_session = (
            await self.storage.list_sessions(self.user_id, worker.id)
        )[0]

        # Initial task is in the worker's inbox.
        inbox = await self.bus.queue_drain(
            MessageBus._INBOX_KEY.format(sid=worker_session.id),
        )
        self.assertEqual(len(inbox), 1)
        _entry_id, payload = inbox[0]
        self.assertEqual(payload["role"], "user")

        # Dispatcher fired chat_service.run(input_msg=None) for worker.
        await asyncio.wait_for(
            self.fake_chat.event_for(worker_session.id).wait(),
            timeout=2.0,
        )
        worker_calls = self.fake_chat.calls_for(worker_session.id)
        self.assertEqual(len(worker_calls), 1)
        self.assertIsNone(worker_calls[0]["input_msg"])
        self.assertEqual(worker_calls[0]["agent_id"], worker.id)

    async def test_send_broadcast_wakes_every_other_member(self) -> None:
        """Broadcast wakes every team participant except the sender."""
        team_id = await self._create_team()
        worker_a = await self.team_service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="alice",
            description="x",
            prompt="hi alice",
            permission_mode="default",
        )
        worker_b = await self.team_service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="bob",
            description="x",
            prompt="hi bob",
            permission_mode="default",
        )
        worker_a_session = (
            await self.storage.list_sessions(self.user_id, worker_a.id)
        )[0]
        worker_b_session = (
            await self.storage.list_sessions(self.user_id, worker_b.id)
        )[0]

        # Wait for the seed wake-ups, then reset state.
        await asyncio.wait_for(
            self.fake_chat.event_for(worker_a_session.id).wait(),
            timeout=2.0,
        )
        await asyncio.wait_for(
            self.fake_chat.event_for(worker_b_session.id).wait(),
            timeout=2.0,
        )
        self.fake_chat.calls.clear()
        self.fake_chat.events.clear()

        # Drain inboxes so we can isolate the broadcast.
        await self.bus.queue_drain(
            MessageBus._INBOX_KEY.format(sid=worker_a_session.id),
        )
        await self.bus.queue_drain(
            MessageBus._INBOX_KEY.format(sid=worker_b_session.id),
        )

        # Leader broadcasts.
        count = await self.team_service.send(
            user_id=self.user_id,
            team_id=team_id,
            from_session_id=self.leader_session_id,
            to=None,
            content="all hands",
        )
        self.assertEqual(count, 2)

        # Both workers got the broadcast in their inboxes.
        a_inbox = await self.bus.queue_drain(
            MessageBus._INBOX_KEY.format(sid=worker_a_session.id),
        )
        b_inbox = await self.bus.queue_drain(
            MessageBus._INBOX_KEY.format(sid=worker_b_session.id),
        )
        self.assertEqual(len(a_inbox), 1)
        self.assertEqual(len(b_inbox), 1)

        # Dispatcher woke both workers (and NOT the leader).
        await asyncio.wait_for(
            self.fake_chat.event_for(worker_a_session.id).wait(),
            timeout=2.0,
        )
        await asyncio.wait_for(
            self.fake_chat.event_for(worker_b_session.id).wait(),
            timeout=2.0,
        )
        self.assertEqual(self.fake_chat.calls_for(self.leader_session_id), [])

    async def test_send_directed_only_wakes_target(self) -> None:
        """``to=<member_id>`` only wakes that one session."""
        team_id = await self._create_team()
        worker_a = await self.team_service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="alice",
            description="x",
            prompt="hi alice",
            permission_mode="default",
        )
        worker_b = await self.team_service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="bob",
            description="x",
            prompt="hi bob",
            permission_mode="default",
        )
        worker_a_session = (
            await self.storage.list_sessions(self.user_id, worker_a.id)
        )[0]
        worker_b_session = (
            await self.storage.list_sessions(self.user_id, worker_b.id)
        )[0]

        # Wait for initial wake-ups, then reset.
        await asyncio.wait_for(
            self.fake_chat.event_for(worker_a_session.id).wait(),
            timeout=2.0,
        )
        await asyncio.wait_for(
            self.fake_chat.event_for(worker_b_session.id).wait(),
            timeout=2.0,
        )
        self.fake_chat.calls.clear()
        self.fake_chat.events.clear()

        # Leader DMs alice only.
        count = await self.team_service.send(
            user_id=self.user_id,
            team_id=team_id,
            from_session_id=self.leader_session_id,
            to=worker_a.id,
            content="just for you",
        )
        self.assertEqual(count, 1)

        # alice woke up; bob did not.
        await asyncio.wait_for(
            self.fake_chat.event_for(worker_a_session.id).wait(),
            timeout=2.0,
        )
        await self._settle()
        self.assertEqual(self.fake_chat.calls_for(worker_b_session.id), [])

    async def test_worker_to_leader_dm_wakes_leader(self) -> None:
        """A worker addressing the leader's agent_id wakes the leader."""
        team_id = await self._create_team()
        worker_a = await self.team_service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="alice",
            description="x",
            prompt="hi",
            permission_mode="default",
        )
        worker_a_session = (
            await self.storage.list_sessions(self.user_id, worker_a.id)
        )[0]

        await asyncio.wait_for(
            self.fake_chat.event_for(worker_a_session.id).wait(),
            timeout=2.0,
        )
        self.fake_chat.calls.clear()
        self.fake_chat.events.clear()

        count = await self.team_service.send(
            user_id=self.user_id,
            team_id=team_id,
            from_session_id=worker_a_session.id,
            to=self.leader.id,
            content="task done",
        )
        self.assertEqual(count, 1)

        await asyncio.wait_for(
            self.fake_chat.event_for(self.leader_session_id).wait(),
            timeout=2.0,
        )
        leader_calls = self.fake_chat.calls_for(self.leader_session_id)
        self.assertEqual(len(leader_calls), 1)
        self.assertEqual(leader_calls[0]["agent_id"], self.leader.id)

    async def test_dissolve_team_storage_cascade(self) -> None:
        """After dissolve the storage cascade tears down the worker
        agent + session; the dispatcher itself doesn't hold any
        per-session state to clean up."""
        team_id = await self._create_team()
        worker = await self.team_service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="alice",
            description="x",
            prompt="hi",
            permission_mode="default",
        )
        worker_session = (
            await self.storage.list_sessions(self.user_id, worker.id)
        )[0]
        worker_sid = worker_session.id

        await asyncio.wait_for(
            self.fake_chat.event_for(worker_sid).wait(),
            timeout=2.0,
        )

        ok = await self.team_service.dissolve_team(self.user_id, team_id)
        self.assertTrue(ok)

        # Storage cascade: team + worker agent + worker session gone.
        self.assertIsNone(
            await self.storage.get_team(self.user_id, team_id),
        )
        self.assertIsNone(
            await self.storage.get_agent(self.user_id, worker.id),
        )
        self.assertIsNone(
            await self.storage.get_session(
                self.user_id,
                worker.id,
                worker_sid,
            ),
        )
        # Leader session continues to exist with team_id cleared.
        leader = await self.storage.get_session(
            self.user_id,
            self.leader.id,
            self.leader_session_id,
        )
        self.assertIsNotNone(leader)
        self.assertIsNone(leader.team_id)
