# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Integration tests for conversation reset orchestration."""

import asyncio
from contextlib import AsyncExitStack
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.app._service import SessionService
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import (
    ChatModelConfig,
    RedisStorage,
    SessionConfig,
    SessionRecord,
    TeamData,
    TeamMember,
    TeamRecord,
)
from agentscope.message import UserMsg
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.state import AgentState


def _make_storage() -> RedisStorage:
    """Create a fakeredis-backed storage instance."""
    storage = RedisStorage.__new__(RedisStorage)
    storage._client = fakeredis.aioredis.FakeRedis(
        decode_responses=True,
    )
    storage.key_ttl = None
    storage.key_config = RedisStorage.KeyConfig()
    return storage


def _session_config(workspace_id: str) -> SessionConfig:
    """Build session configuration whose fields must survive clear."""
    return SessionConfig(
        workspace_id=workspace_id,
        name=f"Conversation {workspace_id}",
        chat_model_config=ChatModelConfig(
            type="openai",
            credential_id="credential",
            model="model",
            parameters={},
        ),
    )


class SessionClearTest(IsolatedAsyncioTestCase):
    """Verify standalone and leader-team clear semantics."""

    async def asyncSetUp(self) -> None:
        self._stack = AsyncExitStack()
        self.storage = _make_storage()
        self._stack.push_async_callback(self.storage._client.aclose)
        self.bus = await self._stack.enter_async_context(InMemoryMessageBus())
        self.service = SessionService(self.storage, self.bus)

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()

    async def _seed_conversation(
        self,
        agent_id: str,
        session_id: str,
        workspace_id: str,
    ) -> PermissionContext:
        """Create one session with durable and transient history."""
        permission = PermissionContext(mode=PermissionMode.DONT_ASK)
        await self.storage.upsert_session(
            "user",
            agent_id,
            _session_config(workspace_id),
            session_id=session_id,
        )
        state = AgentState(
            session_id=session_id,
            summary="summary",
            context=[UserMsg(name="user", content="context")],
            permission_context=permission,
            middle_context={"memory": True},
        )
        await self.storage.update_session_state(
            "user",
            agent_id,
            session_id,
            state,
        )
        await self.storage.upsert_message(
            "user",
            session_id,
            UserMsg(name="user", content="history"),
        )
        await self.bus.queue_push(
            MessageBusKeys.inbox(session_id),
            {"stale": True},
        )
        await self.bus.log_append(
            MessageBusKeys.session_events(session_id),
            {"type": "old"},
        )
        return permission

    async def test_standalone_clear_preserves_configuration(self) -> None:
        """A clear resets history but keeps identity and permissions."""
        permission = await self._seed_conversation(
            "agent",
            "session",
            "workspace",
        )

        cleared = await self.service.clear_conversation(
            "user",
            "agent",
            "session",
        )

        record = await self.storage.get_session(
            "user",
            "agent",
            "session",
        )
        messages = await self.storage.list_messages("user", "session")
        events = await self.bus.log_read(
            MessageBusKeys.session_events("session"),
        )
        self.assertEqual(cleared, ("session",))
        self.assertEqual(record.config, _session_config("workspace"))
        self.assertEqual(record.conversation_revision, 1)
        self.assertEqual(record.state.session_id, "session")
        self.assertEqual(record.state.context, [])
        self.assertEqual(record.state.summary, "")
        self.assertEqual(record.state.middle_context, {})
        self.assertEqual(record.state.permission_context, permission)
        self.assertEqual(messages, ([], False))
        self.assertEqual(
            [event[1]["name"] for event in events],
            ["session_cleared"],
        )
        self.assertEqual(
            await self.bus.queue_drain(
                MessageBusKeys.inbox("session"),
            ),
            [],
        )

    async def test_leader_clear_cascades_to_current_workers(self) -> None:
        """Clearing a leader resets the whole current team roster."""
        await self._seed_conversation(
            "leader-agent",
            "leader-session",
            "leader-workspace",
        )
        await self._seed_conversation(
            "worker-agent",
            "worker-session",
            "worker-workspace",
        )
        team = TeamRecord(
            id="team",
            user_id="user",
            session_id="leader-session",
            leader_agent_id="leader-agent",
            data=TeamData(
                name="team",
                members=[
                    TeamMember(
                        owner_id="user",
                        agent_id="worker-agent",
                        session_id="worker-session",
                        role="invited",
                    ),
                ],
            ),
        )
        await self.storage.upsert_team("user", team)
        await self.storage.set_session_team_id(
            "user",
            "leader-session",
            team.id,
        )
        await self.storage.set_session_team_id(
            "user",
            "worker-session",
            team.id,
        )

        cleared = await self.service.clear_conversation(
            "user",
            "leader-agent",
            "leader-session",
        )

        leader = await self.storage.get_session(
            "user",
            "leader-agent",
            "leader-session",
        )
        worker = await self.storage.get_session(
            "user",
            "worker-agent",
            "worker-session",
        )
        self.assertEqual(
            cleared,
            ("leader-session", "worker-session"),
        )
        self.assertEqual(leader.conversation_revision, 1)
        self.assertEqual(worker.conversation_revision, 1)
        self.assertEqual(leader.team_id, team.id)
        self.assertEqual(worker.team_id, team.id)
        self.assertEqual(leader.state.context, [])
        self.assertEqual(worker.state.context, [])
        self.assertEqual(
            await self.storage.list_messages("user", "leader-session"),
            ([], False),
        )
        self.assertEqual(
            await self.storage.list_messages("user", "worker-session"),
            ([], False),
        )

    async def test_concurrent_clears_serialize_and_remove_barriers(
        self,
    ) -> None:
        """Concurrent clears both complete without losing a barrier."""
        await self._seed_conversation(
            "agent",
            "session",
            "workspace",
        )

        results = await asyncio.gather(
            self.service.clear_conversation(
                "user",
                "agent",
                "session",
            ),
            self.service.clear_conversation(
                "user",
                "agent",
                "session",
            ),
        )

        record = await self.storage.get_session(
            "user",
            "agent",
            "session",
        )
        barriers = await self.bus.registry_getall(
            MessageBusKeys.session_reset_barrier("session"),
        )
        self.assertEqual(results, [("session",), ("session",)])
        self.assertEqual(record.conversation_revision, 2)
        self.assertEqual(record.state.context, [])
        self.assertEqual(barriers, {})

    async def test_cancellation_after_commit_finishes_cleanup(self) -> None:
        """Cancellation waits for cleanup and the clear event after commit."""
        await self._seed_conversation(
            "agent",
            "session",
            "workspace",
        )
        original_reset = self.storage.reset_session_conversation
        commit_finished = asyncio.Event()
        release_commit = asyncio.Event()

        async def _delayed_reset(
            *args: object,
            **kwargs: object,
        ) -> SessionRecord:
            updated = await original_reset(*args, **kwargs)
            commit_finished.set()
            await release_commit.wait()
            return updated

        self.storage.reset_session_conversation = _delayed_reset
        clear_task = asyncio.create_task(
            self.service.clear_conversation(
                "user",
                "agent",
                "session",
            ),
        )
        await asyncio.wait_for(commit_finished.wait(), timeout=1)

        clear_task.cancel()
        release_commit.set()
        with self.assertRaises(asyncio.CancelledError):
            await clear_task

        record = await self.storage.get_session(
            "user",
            "agent",
            "session",
        )
        events = await self.bus.log_read(
            MessageBusKeys.session_events("session"),
        )
        self.assertEqual(record.conversation_revision, 1)
        self.assertEqual(
            await self.bus.queue_drain(MessageBusKeys.inbox("session")),
            [],
        )
        self.assertEqual(
            [event[1]["name"] for event in events],
            ["session_cleared"],
        )
        self.assertEqual(
            await self.bus.registry_getall(
                MessageBusKeys.session_reset_barrier("session"),
            ),
            {},
        )

    async def test_barrier_is_removed_after_session_lock(self) -> None:
        """Do not remove the barrier while clear holds the session lock."""
        await self._seed_conversation(
            "agent",
            "session",
            "workspace",
        )
        barrier_key = MessageBusKeys.session_reset_barrier("session")
        session_lock = MessageBusKeys.session_lock("session")
        original_registry_del = self.bus.registry_del
        lock_states: list[bool] = []

        async def _tracked_registry_del(key: str, field: str) -> None:
            if key == barrier_key:
                lock_states.append(await self.bus.is_locked(session_lock))
            await original_registry_del(key, field)

        self.bus.registry_del = _tracked_registry_del

        await self.service.clear_conversation(
            "user",
            "agent",
            "session",
        )

        self.assertEqual(lock_states, [False])

    async def test_initial_barrier_cancellation_cleans_written_field(
        self,
    ) -> None:
        """Clean a barrier written just before its initial set is cancelled."""
        await self._seed_conversation(
            "agent",
            "session",
            "workspace",
        )
        original_registry_set = self.bus.registry_set

        async def _set_then_cancel(
            namespace: str,
            field: str,
            value: str,
            *,
            ttl_secs: int | None = None,
        ) -> None:
            await original_registry_set(
                namespace,
                field,
                value,
                ttl_secs=ttl_secs,
            )
            raise asyncio.CancelledError

        self.bus.registry_set = _set_then_cancel

        with self.assertRaises(asyncio.CancelledError):
            await self.service.clear_conversation(
                "user",
                "agent",
                "session",
            )

        self.assertEqual(
            await self.bus.registry_getall(
                MessageBusKeys.session_reset_barrier("session"),
            ),
            {},
        )

    async def test_stale_team_member_does_not_block_resolved_targets(
        self,
    ) -> None:
        """Report a stale roster entry after resetting healthy targets."""
        await self._seed_conversation(
            "leader-agent",
            "leader-session",
            "leader-workspace",
        )
        await self._seed_conversation(
            "worker-agent",
            "worker-session",
            "worker-workspace",
        )
        team = TeamRecord(
            id="team",
            user_id="user",
            session_id="leader-session",
            leader_agent_id="leader-agent",
            data=TeamData(
                name="team",
                members=[
                    TeamMember(
                        owner_id="user",
                        agent_id="worker-agent",
                        session_id="worker-session",
                        role="invited",
                    ),
                    TeamMember(
                        owner_id="user",
                        agent_id="missing-agent",
                        session_id="missing-session",
                        role="invited",
                    ),
                ],
            ),
        )
        await self.storage.upsert_team("user", team)
        await self.storage.set_session_team_id(
            "user",
            "leader-session",
            team.id,
        )
        await self.storage.set_session_team_id(
            "user",
            "worker-session",
            team.id,
        )

        with self.assertRaisesRegex(RuntimeError, "1 session"):
            await self.service.clear_conversation(
                "user",
                "leader-agent",
                "leader-session",
            )

        leader = await self.storage.get_session(
            "user",
            "leader-agent",
            "leader-session",
        )
        worker = await self.storage.get_session(
            "user",
            "worker-agent",
            "worker-session",
        )
        self.assertEqual(leader.conversation_revision, 1)
        self.assertEqual(worker.conversation_revision, 1)
        self.assertEqual(leader.state.context, [])
        self.assertEqual(worker.state.context, [])
