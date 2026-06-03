# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for the TeamService orchestration layer.

Covers create_team / add_member / dissolve_team / send. The storage
layer is the real fakeredis-backed RedisStorage; the message bus is a
small in-memory fake so we can introspect what TeamService delivers.
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import TeamService
from agentscope.app import MessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ChatModelConfig,
    RedisKeyConfig,
    RedisStorage,
    SessionConfig,
)


class _FakeBus(MessageBus):
    """In-memory bus capturing every queue_push and publish call.

    Each call also appends to ``timeline`` so tests can assert relative
    ordering against fake-listener calls.
    """

    def __init__(self, timeline: list[tuple[str, str]]) -> None:
        self.queues: dict[str, list[dict]] = {}
        self.published: list[tuple[str, dict]] = []
        self.timeline = timeline

    async def queue_push(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        self.queues.setdefault(key, []).append(payload)
        self.timeline.append(("bus.queue_push", key))
        return f"id-{len(self.queues[key])}"

    async def queue_drain(
        self,
        key: str,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        msgs = self.queues.get(key, [])
        head = list(enumerate(msgs[:max_count]))
        self.queues[key] = msgs[max_count:]
        return [(f"id-{i}", p) for i, p in head]

    async def log_append(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
        max_len: int | None = None,
    ) -> str:
        return ""

    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        return []

    async def log_trim(
        self,
        key: str,
        before_id: str | None = None,
    ) -> None:
        pass

    async def publish(self, key: str, payload: dict) -> None:
        self.published.append((key, payload))
        self.timeline.append(("bus.publish", key))

    async def subscribe(
        self,
        key: str,
        *,
        on_ready=None,
    ) -> AsyncGenerator[dict, None]:
        if on_ready is not None:
            on_ready()
        if False:
            yield {}

    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> AsyncGenerator[None, None]:
        yield

    async def is_locked(self, key: str) -> bool:
        return False


def make_storage() -> RedisStorage:
    """Create a RedisStorage backed by fakeredis."""
    s = RedisStorage.__new__(RedisStorage)
    s._client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    s.key_ttl = None
    s.key_config = RedisKeyConfig()
    return s


def make_leader_agent(user_id: str) -> AgentRecord:
    """Build a regular user-created leader agent."""
    return AgentRecord(
        user_id=user_id,
        data=AgentData(
            name="leader-agent",
            system_prompt="You're a helpful leader.",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


def make_session_config() -> SessionConfig:
    """Session config with a chat model so worker inheritance is
    observable."""
    return SessionConfig(
        workspace_id="ws-1",
        chat_model_config=ChatModelConfig(
            type="openai",
            credential_id="cred-1",
            model="gpt-4",
            parameters={},
        ),
    )


class TestTeamService(IsolatedAsyncioTestCase):
    """End-to-end tests for the team service against fakeredis."""

    async def asyncSetUp(self) -> None:
        """Set up storage + bus + service + a single leader session."""
        self.user_id = "user-1"
        self.storage = make_storage()
        self.timeline: list[tuple[str, str]] = []
        self.bus = _FakeBus(self.timeline)
        self.service = TeamService(
            storage=self.storage,
            message_bus=self.bus,
        )

        # Create a leader agent + session
        self.leader = make_leader_agent(self.user_id)
        await self.storage.upsert_agent(self.user_id, self.leader)
        leader_session = await self.storage.upsert_session(
            self.user_id,
            self.leader.id,
            make_session_config(),
        )
        self.leader_session_id = leader_session.id

    # ---- create_team -------------------------------------------------

    async def test_create_team_persists_and_stamps_team_id(self) -> None:
        """create_team writes a TeamRecord and stamps the leader."""
        team = await self.service.create_team(
            user_id=self.user_id,
            leader_session_id=self.leader_session_id,
            leader_agent_id=self.leader.id,
            name="Research squad",
            description="Find facts about ducks.",
        )
        self.assertEqual(team.data.name, "Research squad")
        self.assertEqual(team.data.description, "Find facts about ducks.")
        self.assertEqual(team.data.member_ids, [])

        # Persisted
        loaded = await self.storage.get_team(self.user_id, team.id)
        self.assertIsNotNone(loaded)

        # Leader session stamped
        leader = await self.storage.get_session(
            self.user_id,
            self.leader.id,
            self.leader_session_id,
        )
        self.assertEqual(leader.team_id, team.id)

    async def test_create_team_rejects_session_already_in_team(
        self,
    ) -> None:
        """A session that already leads a team cannot create another."""
        await self.service.create_team(
            user_id=self.user_id,
            leader_session_id=self.leader_session_id,
            leader_agent_id=self.leader.id,
            name="Team A",
            description="",
        )
        with self.assertRaises(ValueError):
            await self.service.create_team(
                user_id=self.user_id,
                leader_session_id=self.leader_session_id,
                leader_agent_id=self.leader.id,
                name="Team B",
                description="",
            )

    async def test_create_team_rejects_unknown_session(self) -> None:
        """Unknown leader session raises ValueError."""
        with self.assertRaises(ValueError):
            await self.service.create_team(
                user_id=self.user_id,
                leader_session_id="no-such",
                leader_agent_id=self.leader.id,
                name="x",
                description="",
            )

    # ---- add_member --------------------------------------------------

    async def _create_team_for_test(self) -> str:
        team = await self.service.create_team(
            user_id=self.user_id,
            leader_session_id=self.leader_session_id,
            leader_agent_id=self.leader.id,
            name="Squad",
            description="charter",
        )
        return team.id

    async def test_add_member_spawns_worker_and_seeds_inbox(self) -> None:
        """add_member creates a source=team agent + session and pushes
        the initial prompt to the worker's inbox + publishes trigger."""
        team_id = await self._create_team_for_test()
        worker = await self.service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="alice",
            description="Researcher",
            prompt="Find five facts about ducks.",
            permission_mode="explore",
        )

        # Worker agent persisted with source=team
        loaded_agent = await self.storage.get_agent(self.user_id, worker.id)
        self.assertIsNotNone(loaded_agent)
        self.assertEqual(loaded_agent.source, "team")
        # Identity + role baked into system_prompt
        self.assertIn("alice", loaded_agent.data.system_prompt)
        self.assertIn("Researcher", loaded_agent.data.system_prompt)
        self.assertIn("charter", loaded_agent.data.system_prompt)

        # Worker session exists, team_id set, model config inherited
        sessions = await self.storage.list_sessions(self.user_id, worker.id)
        self.assertEqual(len(sessions), 1)
        worker_session = sessions[0]
        self.assertEqual(worker_session.team_id, team_id)
        self.assertEqual(
            worker_session.config.chat_model_config.model,
            "gpt-4",
        )
        # Permission mode wired into state
        self.assertEqual(
            worker_session.state.permission_context.mode.value,
            "explore",
        )

        # Team's member_ids appended
        team = await self.storage.get_team(self.user_id, team_id)
        self.assertEqual(team.data.member_ids, [worker.id])

        # Inbox got the initial task
        inbox = self.bus.queues[
            MessageBus._INBOX_KEY.format(sid=worker_session.id)
        ]
        self.assertEqual(len(inbox), 1)
        msg = inbox[0]
        self.assertEqual(msg["role"], "user")
        # The leader's display name is the sender's name on the message.
        self.assertEqual(msg["name"], "leader-agent")
        # Content contains the task text
        text = "".join(
            b["text"] for b in msg["content"] if b.get("type") == "text"
        )
        self.assertEqual(text, "Find five facts about ducks.")

        # Wakeup signal published (consumer-blind — same channel for all)
        published_keys = [k for k, _ in self.bus.published]
        self.assertIn("agentscope:wakeup_signal", published_keys)
        # And a wakeup entry was enqueued for this worker session
        wakeups = self.bus.queues.get("agentscope:wakeups", [])
        self.assertTrue(
            any(w["session_id"] == worker_session.id for w in wakeups),
        )

    async def test_add_member_rejects_unknown_team(self) -> None:
        """add_member on a missing team raises ValueError."""
        with self.assertRaises(ValueError):
            await self.service.add_member(
                user_id=self.user_id,
                team_id="no-such",
                name="alice",
                description="x",
                prompt="hi",
                permission_mode="default",
            )

    async def test_add_member_rejects_bad_permission_mode(self) -> None:
        """Invalid permission_mode raises ValueError."""
        team_id = await self._create_team_for_test()
        with self.assertRaises(ValueError):
            await self.service.add_member(
                user_id=self.user_id,
                team_id=team_id,
                name="alice",
                description="x",
                prompt="hi",
                permission_mode="lol-no",
            )

    # ---- dissolve_team ----------------------------------------------

    async def test_dissolve_team_removes_team_and_workers(self) -> None:
        """dissolve_team delegates to storage cascade."""
        team_id = await self._create_team_for_test()
        worker = await self.service.add_member(
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

        ok = await self.service.dissolve_team(self.user_id, team_id)
        self.assertTrue(ok)

        self.assertIsNone(
            await self.storage.get_team(self.user_id, team_id),
        )
        self.assertIsNone(
            await self.storage.get_agent(self.user_id, worker.id),
        )
        leader = await self.storage.get_session(
            self.user_id,
            self.leader.id,
            self.leader_session_id,
        )
        self.assertIsNone(leader.team_id)
        # Worker session also gone via cascade
        self.assertIsNone(
            await self.storage.get_session(
                self.user_id,
                worker.id,
                worker_session.id,
            ),
        )

    async def test_dissolve_team_returns_false_for_unknown(self) -> None:
        """dissolve_team returns False when the team does not exist."""
        ok = await self.service.dissolve_team(self.user_id, "no-such")
        self.assertFalse(ok)

    # ---- send -------------------------------------------------------

    async def test_send_broadcast_excludes_sender(self) -> None:
        """Broadcast delivers to every participant except the sender."""
        team_id = await self._create_team_for_test()
        a = await self.service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="alice",
            description="x",
            prompt="hi",
            permission_mode="default",
        )
        b = await self.service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="bob",
            description="x",
            prompt="hi",
            permission_mode="default",
        )
        a_session = (await self.storage.list_sessions(self.user_id, a.id))[0]
        b_session = (await self.storage.list_sessions(self.user_id, b.id))[0]

        # Drain the seed messages so we can isolate the broadcast.
        self.bus.queues.clear()
        self.bus.published.clear()

        # Leader broadcasts.
        count = await self.service.send(
            user_id=self.user_id,
            team_id=team_id,
            from_session_id=self.leader_session_id,
            to=None,
            content="all hands meeting",
        )
        self.assertEqual(count, 2)

        # Both workers got the message; the leader did not (it's the sender).
        self.assertIn(
            MessageBus._INBOX_KEY.format(sid=a_session.id),
            self.bus.queues,
        )
        self.assertIn(
            MessageBus._INBOX_KEY.format(sid=b_session.id),
            self.bus.queues,
        )
        self.assertNotIn(
            MessageBus._INBOX_KEY.format(sid=self.leader_session_id),
            self.bus.queues,
        )

        # Wakeup entries enqueued for both recipients (no per-session
        # channels — all wakeups go to the shared queue).
        wakeups = self.bus.queues.get("agentscope:wakeups", [])
        target_sessions = {w["session_id"] for w in wakeups}
        self.assertEqual(
            target_sessions,
            {a_session.id, b_session.id},
        )

    async def test_send_directed_targets_one_member(self) -> None:
        """``to=<agent_id>`` delivers to that one session."""
        team_id = await self._create_team_for_test()
        a = await self.service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="alice",
            description="x",
            prompt="hi",
            permission_mode="default",
        )
        a_session = (await self.storage.list_sessions(self.user_id, a.id))[0]

        self.bus.queues.clear()
        self.bus.published.clear()

        count = await self.service.send(
            user_id=self.user_id,
            team_id=team_id,
            from_session_id=self.leader_session_id,
            to=a.id,
            content="for you only",
        )
        self.assertEqual(count, 1)
        self.assertIn(
            MessageBus._INBOX_KEY.format(sid=a_session.id),
            self.bus.queues,
        )
        # No OTHER inbox touched (the shared wakeup queue also has
        # one entry — that's expected and not a per-session inbox).
        inbox_keys = [k for k in self.bus.queues if "inbox:" in k]
        self.assertEqual(
            inbox_keys,
            [MessageBus._INBOX_KEY.format(sid=a_session.id)],
        )

    async def test_send_worker_to_leader(self) -> None:
        """A worker can address the leader by the leader's agent_id."""
        team_id = await self._create_team_for_test()
        a = await self.service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="alice",
            description="x",
            prompt="hi",
            permission_mode="default",
        )
        a_session = (await self.storage.list_sessions(self.user_id, a.id))[0]

        self.bus.queues.clear()
        self.bus.published.clear()

        count = await self.service.send(
            user_id=self.user_id,
            team_id=team_id,
            from_session_id=a_session.id,
            to=self.leader.id,
            content="task done",
        )
        self.assertEqual(count, 1)
        self.assertIn(
            MessageBus._INBOX_KEY.format(sid=self.leader_session_id),
            self.bus.queues,
        )

    async def test_send_rejects_self_target(self) -> None:
        """Cannot direct-send to your own session."""
        team_id = await self._create_team_for_test()
        with self.assertRaises(ValueError):
            await self.service.send(
                user_id=self.user_id,
                team_id=team_id,
                from_session_id=self.leader_session_id,
                to=self.leader.id,
                content="hi me",
            )

    async def test_send_rejects_unknown_recipient(self) -> None:
        """Recipient must be a member of the team."""
        team_id = await self._create_team_for_test()
        with self.assertRaises(ValueError):
            await self.service.send(
                user_id=self.user_id,
                team_id=team_id,
                from_session_id=self.leader_session_id,
                to="not-a-member",
                content="hi",
            )

    async def test_send_rejects_outside_sender(self) -> None:
        """Sender that is not part of the team is rejected."""
        team_id = await self._create_team_for_test()
        # Make a second leader+session that is NOT in the team.
        other_leader = make_leader_agent(self.user_id)
        await self.storage.upsert_agent(self.user_id, other_leader)
        other_session = await self.storage.upsert_session(
            self.user_id,
            other_leader.id,
            make_session_config(),
        )
        with self.assertRaises(ValueError):
            await self.service.send(
                user_id=self.user_id,
                team_id=team_id,
                from_session_id=other_session.id,
                to=None,
                content="hi",
            )

    # ---- wakeup wiring ---------------------------------------------

    async def test_add_member_enqueues_wakeup_for_worker(self) -> None:
        """add_member pushes a wakeup entry on the shared queue +
        publishes the wakeup signal so a dispatcher kicks off."""
        team_id = await self._create_team_for_test()
        # Reset timeline to focus on add_member only.
        self.timeline.clear()

        worker = await self.service.add_member(
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

        # The shared wakeup queue got an entry pointing at the worker.
        wakeup_entries = self.bus.queues.get("agentscope:wakeups", [])
        self.assertEqual(len(wakeup_entries), 1)
        self.assertEqual(wakeup_entries[0]["session_id"], worker_session.id)
        self.assertEqual(wakeup_entries[0]["user_id"], self.user_id)
        self.assertEqual(wakeup_entries[0]["agent_id"], worker.id)

        # The wakeup signal was published.
        self.assertIn(
            ("bus.publish", "agentscope:wakeup_signal"),
            self.timeline,
        )

    async def test_send_broadcast_enqueues_wakeup_per_recipient(
        self,
    ) -> None:
        """A broadcast pushes one wakeup entry per non-sender member."""
        team_id = await self._create_team_for_test()
        a = await self.service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="alice",
            description="x",
            prompt="hi alice",
            permission_mode="default",
        )
        b = await self.service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="bob",
            description="x",
            prompt="hi bob",
            permission_mode="default",
        )
        a_session = (await self.storage.list_sessions(self.user_id, a.id))[0]
        b_session = (await self.storage.list_sessions(self.user_id, b.id))[0]

        # Drop the seed wakeups so we can isolate the broadcast.
        self.bus.queues["agentscope:wakeups"] = []
        self.bus.published.clear()
        self.timeline.clear()

        count = await self.service.send(
            user_id=self.user_id,
            team_id=team_id,
            from_session_id=self.leader_session_id,
            to=None,
            content="all hands",
        )
        self.assertEqual(count, 2)

        # Two wakeups, for the two workers (sender excluded).
        wakeups = self.bus.queues.get("agentscope:wakeups", [])
        target_sessions = sorted(w["session_id"] for w in wakeups)
        self.assertEqual(
            target_sessions,
            sorted([a_session.id, b_session.id]),
        )

    async def test_dissolve_team_does_not_touch_wakeup_queue(self) -> None:
        """Dissolve is a pure storage cascade — it does not produce
        any wake-up entries (workers are gone; nothing to wake)."""
        team_id = await self._create_team_for_test()
        await self.service.add_member(
            user_id=self.user_id,
            team_id=team_id,
            name="alice",
            description="x",
            prompt="hi",
            permission_mode="default",
        )

        self.bus.queues["agentscope:wakeups"] = []
        self.bus.published.clear()
        self.timeline.clear()

        ok = await self.service.dissolve_team(self.user_id, team_id)
        self.assertTrue(ok)

        self.assertEqual(self.bus.queues.get("agentscope:wakeups", []), [])
        self.assertEqual(self.bus.published, [])
        self.assertIsNone(
            await self.storage.get_team(self.user_id, team_id),
        )
