# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for RedisStorage using fakeredis."""

from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.app.storage import (
    RedisStorage,
    AgentRecord,
    SessionConfig,
    SessionRecord,
    ChatModelConfig,
    ScheduleRecord,
    ScheduleData,
    SessionSource,
    TeamData,
    TeamRecord,
)
from agentscope.credential import OllamaCredential
from agentscope.app.storage import AgentData
from agentscope.agent import ContextConfig, ReActConfig
from agentscope.message import UserMsg, AssistantMsg, TextBlock
from agentscope.state import AgentState


def make_storage() -> RedisStorage:
    """Create a RedisStorage instance backed by fakeredis."""
    storage = RedisStorage.__new__(RedisStorage)
    # pylint: disable=protected-access
    storage._client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage.key_ttl = None
    storage.key_config = RedisStorage.KeyConfig()
    return storage


def make_agent_record(user_id: str) -> AgentRecord:
    """Create a test AgentRecord with all-default sub-configs."""
    return AgentRecord(
        user_id=user_id,
        data=AgentData(
            id="agent-data-id",
            name="test-agent",
            system_prompt="You are a helpful assistant.",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


def make_session_config(workspace_id: str = "ws-1") -> SessionConfig:
    """Create a test SessionConfig with a chat model config."""
    return SessionConfig(
        workspace_id=workspace_id,
        chat_model_config=ChatModelConfig(
            type="openai",
            credential_id="cred-1",
            model="gpt-4",
            parameters={},
        ),
    )


class TestCredential(IsolatedAsyncioTestCase):
    """Tests for credential CRUD and cascading operations."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"

    async def test_create(self) -> None:
        """Create a credential and verify it is retrievable via list."""
        cred_id = await self.storage.upsert_credential(
            self.user_id,
            OllamaCredential(host="http://localhost:11434"),
        )
        records = await self.storage.list_credentials(self.user_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, cred_id)
        self.assertEqual(records[0].data.get("type"), "ollama_credential")
        self.assertEqual(
            records[0].data.get("host"),
            "http://localhost:11434",
        )

    async def test_list_empty(self) -> None:
        """Verify list returns empty when no records exist."""
        records = await self.storage.list_credentials(self.user_id)
        self.assertEqual(records, [])

    async def test_update_in_place(self) -> None:
        """Update a credential and verify data changed without adding
        a new record."""
        cred_id = await self.storage.upsert_credential(
            self.user_id,
            OllamaCredential(host="http://old-host:11434"),
        )
        await self.storage.upsert_credential(
            self.user_id,
            OllamaCredential(id=cred_id, host="http://new-host:11434"),
        )
        records = await self.storage.list_credentials(self.user_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].data.get("host"), "http://new-host:11434")

    async def test_delete(self) -> None:
        """Delete a credential and verify it is gone from Redis."""
        cred_id = await self.storage.upsert_credential(
            self.user_id,
            OllamaCredential(host="http://localhost:11434"),
        )
        result = await self.storage.delete_credential(self.user_id, cred_id)
        self.assertTrue(result)
        records = await self.storage.list_credentials(self.user_id)
        self.assertEqual(records, [])

    async def test_delete_nonexistent(self) -> None:
        """Verify delete returns False for non-existent record."""
        result = await self.storage.delete_credential(
            self.user_id,
            "no-such-id",
        )
        self.assertFalse(result)

    async def test_user_isolation(self) -> None:
        """Verify different users cannot see each other's records."""
        await self.storage.upsert_credential(
            "user-A",
            OllamaCredential(host="http://localhost:11434"),
        )
        records = await self.storage.list_credentials("user-B")
        self.assertEqual(records, [])


class TestAgent(IsolatedAsyncioTestCase):
    """Tests for agent CRUD and cascading operations."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"

    async def test_create(self) -> None:
        """Create an agent and verify it is retrievable via list."""
        record = make_agent_record(self.user_id)
        agent_id = await self.storage.upsert_agent(self.user_id, record)
        records = await self.storage.list_agents(self.user_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, agent_id)
        self.assertEqual(records[0].data.name, "test-agent")

    async def test_list_empty(self) -> None:
        """Verify list returns empty when no records exist."""
        records = await self.storage.list_agents(self.user_id)
        self.assertEqual(records, [])

    async def test_delete(self) -> None:
        """Delete an agent and verify it is gone from Redis."""
        record = make_agent_record(self.user_id)
        await self.storage.upsert_agent(self.user_id, record)
        result = await self.storage.delete_agent(self.user_id, record.id)
        self.assertTrue(result)
        records = await self.storage.list_agents(self.user_id)
        self.assertEqual(records, [])

    async def test_delete_nonexistent(self) -> None:
        """Verify delete returns False for non-existent record."""
        result = await self.storage.delete_agent(self.user_id, "no-such-id")
        self.assertFalse(result)

    async def test_user_isolation(self) -> None:
        """Verify different users cannot see each other's records."""
        await self.storage.upsert_agent("user-A", make_agent_record("user-A"))
        records = await self.storage.list_agents("user-B")
        self.assertEqual(records, [])


class TestSession(IsolatedAsyncioTestCase):
    """Tests for session CRUD and cascading operations."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"
        self.workspace_id = "ws-1"

    async def test_create(self) -> None:
        """Create a session and verify it is retrievable via list."""
        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(self.workspace_id),
        )
        records = await self.storage.list_sessions(self.user_id, self.agent_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].config.workspace_id, self.workspace_id)
        self.assertEqual(records[0].agent_id, self.agent_id)

    async def test_list_empty(self) -> None:
        """Verify list returns empty when no records exist."""
        records = await self.storage.list_sessions(self.user_id, self.agent_id)
        self.assertEqual(records, [])

    async def test_upsert_same_triple_updates_in_place(self) -> None:
        """Second upsert with the same session_id must update the existing
        record, not create a second one."""
        session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(self.workspace_id),
        )
        first_id = session.id

        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(self.workspace_id),
            session_id=first_id,
        )
        records_after = await self.storage.list_sessions(
            self.user_id,
            self.agent_id,
        )
        self.assertEqual(len(records_after), 1)
        self.assertEqual(records_after[0].id, first_id)

    async def test_create_with_explicit_session_id_uses_that_key(self) -> None:
        """A caller-provided session_id should be the stored record id."""
        session_id = "session-from-router"

        session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(self.workspace_id),
            session_id=session_id,
        )

        self.assertEqual(session.id, session_id)
        fetched = await self.storage.get_session(
            self.user_id,
            self.agent_id,
            session_id,
        )
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, session_id)

        await self.storage.update_session_state(
            self.user_id,
            self.agent_id,
            session_id,
            AgentState(),
        )
        records = await self.storage.list_sessions(self.user_id, self.agent_id)
        self.assertEqual([record.id for record in records], [session_id])

    async def test_delete(self) -> None:
        """Delete a session and verify it is gone from Redis."""
        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(self.workspace_id),
        )
        records = await self.storage.list_sessions(self.user_id, self.agent_id)
        result = await self.storage.delete_session(
            self.user_id,
            self.agent_id,
            records[0].id,
        )
        self.assertTrue(result)
        remaining = await self.storage.list_sessions(
            self.user_id,
            self.agent_id,
        )
        self.assertEqual(remaining, [])

    async def test_delete_cascades_lookup_key(self) -> None:
        """Deleting a session must remove the lookup key so a subsequent upsert
        for the same (user, agent) pair creates a fresh session with a new
        id."""
        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(self.workspace_id),
        )
        records = await self.storage.list_sessions(self.user_id, self.agent_id)
        old_id = records[0].id

        await self.storage.delete_session(self.user_id, self.agent_id, old_id)

        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(self.workspace_id),
        )
        new_records = await self.storage.list_sessions(
            self.user_id,
            self.agent_id,
        )
        self.assertEqual(len(new_records), 1)
        self.assertNotEqual(new_records[0].id, old_id)

    async def test_delete_nonexistent(self) -> None:
        """Verify delete returns False for non-existent record."""
        result = await self.storage.delete_session(
            self.user_id,
            self.agent_id,
            "no-such-id",
        )
        self.assertFalse(result)

    async def test_agent_isolation(self) -> None:
        """Verify different agents cannot see each other's sessions."""
        await self.storage.upsert_session(
            self.user_id,
            "agent-A",
            make_session_config(self.workspace_id),
        )
        records = await self.storage.list_sessions(self.user_id, "agent-B")
        self.assertEqual(records, [])


class TestMessage(IsolatedAsyncioTestCase):
    """Tests for message persistence: upsert_message, get_message and
    list_messages."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"
        self.session_id = "session-1"

    async def test_upsert_appends_new_message(self) -> None:
        """Upserting a new message appends it to the session list."""
        msg = UserMsg(name="alice", content="hello")
        await self.storage.upsert_message(self.user_id, self.session_id, msg)
        messages = await self.storage.list_messages(
            self.user_id,
            self.session_id,
        )
        self.assertListEqual(
            [m.model_dump() for m in messages],
            [msg.model_dump()],
        )

    async def test_upsert_refreshes_message_list_ttl(self) -> None:
        """Message list keys expire with the session storage TTL."""
        self.storage.key_ttl = 60
        msg = UserMsg(name="alice", content="hello")

        await self.storage.upsert_message(self.user_id, self.session_id, msg)

        ttl = await self.storage._client.ttl(
            self.storage._message_key(self.user_id, self.session_id),
        )
        self.assertGreater(ttl, 0)

    async def test_upsert_replaces_last_message_with_same_id(self) -> None:
        """Upserting a message whose id matches the last entry replaces it
        in-place (streaming overwrite), rather than creating a duplicate."""
        msg = AssistantMsg(name="bot", content="v1")
        await self.storage.upsert_message(self.user_id, self.session_id, msg)

        # Keep the same id but replace content — simulates a streaming update.
        updated = msg.model_copy(
            update={"content": [TextBlock(text="v2")]},
        )
        await self.storage.upsert_message(
            self.user_id,
            self.session_id,
            updated,
        )

        messages = await self.storage.list_messages(
            self.user_id,
            self.session_id,
        )
        self.assertListEqual(
            [m.model_dump() for m in messages],
            [updated.model_dump()],
            "Duplicate must not be created; existing entry must be replaced.",
        )

    async def test_replace_refreshes_message_list_ttl(self) -> None:
        """Streaming message replacement keeps the message key expiring."""
        self.storage.key_ttl = 60
        msg = AssistantMsg(name="bot", content="v1")
        await self.storage.upsert_message(self.user_id, self.session_id, msg)
        await self.storage._client.persist(
            self.storage._message_key(self.user_id, self.session_id),
        )
        updated = msg.model_copy(
            update={"content": [TextBlock(text="v2")]},
        )

        await self.storage.upsert_message(
            self.user_id,
            self.session_id,
            updated,
        )

        ttl = await self.storage._client.ttl(
            self.storage._message_key(self.user_id, self.session_id),
        )
        self.assertGreater(ttl, 0)

    async def test_upsert_appends_when_id_differs_from_last(self) -> None:
        """Upserting a message with a different id than the last always
        appends, even if an earlier message shares the same id."""
        msg1 = UserMsg(name="alice", content="first")
        msg2 = UserMsg(name="alice", content="second")
        await self.storage.upsert_message(self.user_id, self.session_id, msg1)
        await self.storage.upsert_message(self.user_id, self.session_id, msg2)
        messages = await self.storage.list_messages(
            self.user_id,
            self.session_id,
        )
        self.assertListEqual(
            [m.model_dump() for m in messages],
            [msg1.model_dump(), msg2.model_dump()],
        )

    async def test_get_message_returns_correct_message(self) -> None:
        """get_message fetches the message matching the given id."""
        msg1 = UserMsg(name="alice", content="first")
        msg2 = UserMsg(name="alice", content="second")
        await self.storage.upsert_message(self.user_id, self.session_id, msg1)
        await self.storage.upsert_message(self.user_id, self.session_id, msg2)

        fetched = await self.storage.get_message(
            self.user_id,
            self.session_id,
            msg1.id,
        )
        self.assertIsNotNone(fetched)
        self.assertDictEqual(fetched.model_dump(), msg1.model_dump())

    async def test_get_message_nonexistent_returns_none(self) -> None:
        """get_message returns None when the message id does not exist."""
        result = await self.storage.get_message(
            self.user_id,
            self.session_id,
            "no-such-id",
        )
        self.assertIsNone(result)

    async def test_list_messages_empty_session(self) -> None:
        """list_messages returns an empty list for a session with no
        messages."""
        messages = await self.storage.list_messages(
            self.user_id,
            self.session_id,
        )
        self.assertListEqual(messages, [])

    async def test_list_messages_pagination(self) -> None:
        """list_messages respects offset and limit parameters."""
        msgs = [UserMsg(name="alice", content=f"msg-{i}") for i in range(5)]
        for m in msgs:
            await self.storage.upsert_message(
                self.user_id,
                self.session_id,
                m,
            )

        # Fetch the middle slice: offset=1, limit=3 → msgs[1], msgs[2], msgs[3]
        page = await self.storage.list_messages(
            self.user_id,
            self.session_id,
            offset=1,
            limit=3,
        )
        self.assertListEqual(
            [m.model_dump() for m in page],
            [m.model_dump() for m in msgs[1:4]],
        )

    async def test_list_messages_order_preserved(self) -> None:
        """Messages are returned in the insertion order (chronological)."""
        msgs = [
            UserMsg(name="alice", content=text)
            for text in ["alpha", "beta", "gamma"]
        ]
        for m in msgs:
            await self.storage.upsert_message(
                self.user_id,
                self.session_id,
                m,
            )
        messages = await self.storage.list_messages(
            self.user_id,
            self.session_id,
        )
        self.assertListEqual(
            [m.model_dump() for m in messages],
            [m.model_dump() for m in msgs],
        )

    async def test_session_isolation(self) -> None:
        """Messages belonging to different sessions do not interfere."""
        await self.storage.upsert_message(
            self.user_id,
            "session-A",
            UserMsg(name="alice", content="in A"),
        )
        messages = await self.storage.list_messages(
            self.user_id,
            "session-B",
        )
        self.assertListEqual(messages, [])


def make_schedule_record(user_id: str, agent_id: str) -> ScheduleRecord:
    """Create a test ScheduleRecord."""
    return ScheduleRecord(
        user_id=user_id,
        agent_id=agent_id,
        data=ScheduleData(
            name="test-schedule",
            cron_expression="0 9 * * *",
            started_at="2026-01-01T00:00:00",
            chat_model_config=ChatModelConfig(
                type="openai",
                credential_id="cred-1",
                model="gpt-4",
                parameters={},
            ),
        ),
    )


class TestScheduleSession(IsolatedAsyncioTestCase):
    """Tests for schedule-session index and cascade deletion."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"

    async def test_list_sessions_by_schedule(self) -> None:
        """Sessions created with source_schedule_id are queryable by
        schedule."""
        schedule = make_schedule_record(self.user_id, self.agent_id)
        await self.storage.upsert_schedule(self.user_id, schedule)

        session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
            source=SessionSource.SCHEDULE,
            source_schedule_id=schedule.id,
        )

        results = await self.storage.list_sessions_by_schedule(
            self.user_id,
            schedule.id,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, session.id)
        self.assertEqual(results[0].source_schedule_id, schedule.id)

    async def test_list_sessions_by_schedule_empty(self) -> None:
        """Returns empty list when no sessions exist for a schedule."""
        results = await self.storage.list_sessions_by_schedule(
            self.user_id,
            "nonexistent-schedule",
        )
        self.assertEqual(results, [])

    async def test_schedule_session_also_in_agent_index(self) -> None:
        """A schedule-created session appears in both the schedule and agent
        session indexes."""
        schedule = make_schedule_record(self.user_id, self.agent_id)
        await self.storage.upsert_schedule(self.user_id, schedule)

        session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
            source=SessionSource.SCHEDULE,
            source_schedule_id=schedule.id,
        )

        agent_sessions = await self.storage.list_sessions(
            self.user_id,
            self.agent_id,
        )
        self.assertEqual(len(agent_sessions), 1)
        self.assertEqual(agent_sessions[0].id, session.id)

    async def test_delete_schedule_cascades_sessions(self) -> None:
        """Deleting a schedule removes all its execution sessions."""
        schedule = make_schedule_record(self.user_id, self.agent_id)
        await self.storage.upsert_schedule(self.user_id, schedule)

        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
            source=SessionSource.SCHEDULE,
            source_schedule_id=schedule.id,
        )
        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
            source=SessionSource.SCHEDULE,
            source_schedule_id=schedule.id,
        )

        await self.storage.delete_schedule(self.user_id, schedule.id)

        schedule_sessions = await self.storage.list_sessions_by_schedule(
            self.user_id,
            schedule.id,
        )
        self.assertEqual(schedule_sessions, [])

        agent_sessions = await self.storage.list_sessions(
            self.user_id,
            self.agent_id,
        )
        self.assertEqual(agent_sessions, [])

    async def test_delete_session_cleans_schedule_index(self) -> None:
        """Deleting a session removes it from the schedule session index."""
        schedule = make_schedule_record(self.user_id, self.agent_id)
        await self.storage.upsert_schedule(self.user_id, schedule)

        session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
            source=SessionSource.SCHEDULE,
            source_schedule_id=schedule.id,
        )

        await self.storage.delete_session(
            self.user_id,
            self.agent_id,
            session.id,
        )

        results = await self.storage.list_sessions_by_schedule(
            self.user_id,
            schedule.id,
        )
        self.assertEqual(results, [])


def make_team_record(
    user_id: str,
    session_id: str = "leader-session-1",
    name: str = "test-team",
    member_ids: list[str] | None = None,
) -> TeamRecord:
    """Create a test TeamRecord."""
    return TeamRecord(
        user_id=user_id,
        session_id=session_id,
        data=TeamData(
            name=name,
            member_ids=member_ids if member_ids is not None else [],
        ),
    )


class TestAgentSource(IsolatedAsyncioTestCase):
    """Tests for the ``source`` field on AgentRecord."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"

    async def test_default_source_is_user(self) -> None:
        """An AgentRecord built without ``source`` defaults to ``"user"``."""
        record = make_agent_record(self.user_id)
        self.assertEqual(record.source, "user")
        await self.storage.upsert_agent(self.user_id, record)
        loaded = await self.storage.get_agent(self.user_id, record.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.source, "user")

    async def test_team_source_round_trips(self) -> None:
        """A worker AgentRecord persists ``source='team'`` through Redis."""
        record = AgentRecord(
            user_id=self.user_id,
            source="team",
            data=AgentData(
                id="worker-1",
                name="worker",
                system_prompt="You are a team worker.",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        await self.storage.upsert_agent(self.user_id, record)
        loaded = await self.storage.get_agent(self.user_id, record.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.source, "team")

    async def test_list_agents_filters_out_team_workers(self) -> None:
        """``storage.list_agents`` returns only ``source='user'`` agents.
        Workers (``source='team'``) are scoped to a team and addressable
        only via direct id lookup."""
        user_agent = make_agent_record(self.user_id)
        worker = AgentRecord(
            id="worker-1",
            user_id=self.user_id,
            source="team",
            data=AgentData(
                id="data-w",
                name="worker",
                system_prompt="",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        await self.storage.upsert_agent(self.user_id, user_agent)
        await self.storage.upsert_agent(self.user_id, worker)

        listed = await self.storage.list_agents(self.user_id)
        listed_ids = {a.id for a in listed}
        self.assertIn(user_agent.id, listed_ids)
        self.assertNotIn(worker.id, listed_ids)

        # But direct lookup still works for the worker.
        loaded_worker = await self.storage.get_agent(
            self.user_id,
            worker.id,
        )
        self.assertIsNotNone(loaded_worker)
        self.assertEqual(loaded_worker.source, "team")

    async def test_legacy_record_without_source_deserializes(self) -> None:
        """JSON written before the ``source`` field was added still loads
        and falls back to the default ``"user"``."""
        legacy_record = make_agent_record(self.user_id)
        legacy_json = legacy_record.model_dump_json()
        # Strip the new field to simulate pre-migration data.
        import json

        payload = json.loads(legacy_json)
        payload.pop("source", None)
        # pylint: disable=protected-access
        key = self.storage._key(
            self.storage.key_config.agent,
            user_id=self.user_id,
            agent_id=legacy_record.id,
        )
        await self.storage._client.set(key, json.dumps(payload))
        await self.storage._client.sadd(
            self.storage._key(
                self.storage.key_config.agent_index,
                user_id=self.user_id,
            ),
            legacy_record.id,
        )

        loaded = await self.storage.get_agent(self.user_id, legacy_record.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.source, "user")


class TestSessionTeamId(IsolatedAsyncioTestCase):
    """Tests for the ``team_id`` field on SessionRecord."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"

    async def test_default_team_id_is_none(self) -> None:
        """A SessionRecord built without ``team_id`` defaults to ``None``."""
        session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
        )
        self.assertIsNone(session.team_id)

        loaded = await self.storage.get_session(
            self.user_id,
            self.agent_id,
            session.id,
        )
        self.assertIsNotNone(loaded)
        self.assertIsNone(loaded.team_id)

    async def test_legacy_session_without_team_id_deserializes(self) -> None:
        """JSON written before ``team_id`` existed still loads with default
        ``None``."""
        session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
        )

        # Strip the new field from the persisted JSON to simulate pre-migration
        # data, then write it back at the same key.
        import json

        # pylint: disable=protected-access
        key = self.storage._key(
            self.storage.key_config.session,
            user_id=self.user_id,
            session_id=session.id,
        )
        raw = await self.storage._client.get(key)
        payload = json.loads(raw)
        payload.pop("team_id", None)
        await self.storage._client.set(key, json.dumps(payload))

        loaded = await self.storage.get_session(
            self.user_id,
            self.agent_id,
            session.id,
        )
        self.assertIsNotNone(loaded)
        self.assertIsNone(loaded.team_id)


class TestTeam(IsolatedAsyncioTestCase):
    """Tests for team CRUD."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"

    async def test_create(self) -> None:
        """Create a team and verify it is retrievable via list."""
        record = make_team_record(self.user_id)
        stored = await self.storage.upsert_team(self.user_id, record)
        records = await self.storage.list_teams(self.user_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, stored.id)
        self.assertEqual(records[0].session_id, "leader-session-1")
        self.assertEqual(records[0].data.name, "test-team")
        self.assertEqual(records[0].data.member_ids, [])

    async def test_list_empty(self) -> None:
        """Verify list returns empty when no teams exist."""
        records = await self.storage.list_teams(self.user_id)
        self.assertEqual(records, [])

    async def test_get_returns_record(self) -> None:
        """get_team returns the persisted record by id."""
        record = make_team_record(
            self.user_id,
            member_ids=["worker-a", "worker-b"],
        )
        await self.storage.upsert_team(self.user_id, record)
        loaded = await self.storage.get_team(self.user_id, record.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.id, record.id)
        self.assertEqual(loaded.data.member_ids, ["worker-a", "worker-b"])

    async def test_get_nonexistent_returns_none(self) -> None:
        """get_team returns None when the id does not exist."""
        loaded = await self.storage.get_team(self.user_id, "no-such-id")
        self.assertIsNone(loaded)

    async def test_update_in_place(self) -> None:
        """Upsert with the same id overwrites the existing record."""
        record = make_team_record(self.user_id, name="original")
        await self.storage.upsert_team(self.user_id, record)

        record.data.name = "renamed"
        record.data.member_ids = ["new-worker"]
        await self.storage.upsert_team(self.user_id, record)

        records = await self.storage.list_teams(self.user_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].data.name, "renamed")
        self.assertEqual(records[0].data.member_ids, ["new-worker"])

    async def test_upsert_refreshes_updated_at(self) -> None:
        """upsert_team bumps ``updated_at`` on each write."""
        record = make_team_record(self.user_id)
        first = await self.storage.upsert_team(self.user_id, record)
        first_updated = first.updated_at

        # Second write must have a strictly non-decreasing updated_at.
        second = await self.storage.upsert_team(self.user_id, record)
        self.assertGreaterEqual(second.updated_at, first_updated)

    async def test_delete(self) -> None:
        """Delete a team and verify it is gone from Redis and the index."""
        record = make_team_record(self.user_id)
        await self.storage.upsert_team(self.user_id, record)

        result = await self.storage.delete_team(self.user_id, record.id)
        self.assertTrue(result)

        loaded = await self.storage.get_team(self.user_id, record.id)
        self.assertIsNone(loaded)
        self.assertEqual(await self.storage.list_teams(self.user_id), [])

    async def test_delete_nonexistent(self) -> None:
        """delete_team returns False for an unknown id."""
        result = await self.storage.delete_team(self.user_id, "no-such-id")
        self.assertFalse(result)

    async def test_user_isolation(self) -> None:
        """Teams from one user are invisible to another."""
        await self.storage.upsert_team(
            "user-A",
            make_team_record("user-A"),
        )
        records = await self.storage.list_teams("user-B")
        self.assertEqual(records, [])


def make_worker_agent(user_id: str, agent_id: str) -> AgentRecord:
    """Create a source='team' worker AgentRecord."""
    return AgentRecord(
        id=agent_id,
        user_id=user_id,
        source="team",
        data=AgentData(
            id=f"data-{agent_id}",
            name=f"name-{agent_id}",
            system_prompt="worker",
            context_config=ContextConfig(),
            react_config=ReActConfig(),
        ),
    )


class TestTeamCascade(IsolatedAsyncioTestCase):
    """End-to-end cascade tests for the team relationship graph."""

    async def asyncSetUp(self) -> None:
        """Build a small team fixture: 1 leader user-agent + 2 workers."""
        self.storage = make_storage()
        self.user_id = "user-1"

        # Leader agent (user-created) and its session
        self.leader_agent = make_agent_record(self.user_id)
        await self.storage.upsert_agent(self.user_id, self.leader_agent)
        leader_session = await self.storage.upsert_session(
            self.user_id,
            self.leader_agent.id,
            make_session_config(),
        )
        self.leader_session_id = leader_session.id

        # Two worker agents and their sessions
        self.worker_a = make_worker_agent(self.user_id, "worker-a")
        self.worker_b = make_worker_agent(self.user_id, "worker-b")
        await self.storage.upsert_agent(self.user_id, self.worker_a)
        await self.storage.upsert_agent(self.user_id, self.worker_b)
        worker_a_session = await self.storage.upsert_session(
            self.user_id,
            self.worker_a.id,
            make_session_config(),
        )
        worker_b_session = await self.storage.upsert_session(
            self.user_id,
            self.worker_b.id,
            make_session_config(),
        )
        self.worker_a_session_id = worker_a_session.id
        self.worker_b_session_id = worker_b_session.id

        # The team itself + leader's team_id back-reference
        self.team = make_team_record(
            self.user_id,
            session_id=self.leader_session_id,
            member_ids=[self.worker_a.id, self.worker_b.id],
        )
        await self.storage.upsert_team(self.user_id, self.team)

        # Stamp team_id on every team-participating session
        # pylint: disable=protected-access
        for sid in [
            self.leader_session_id,
            self.worker_a_session_id,
            self.worker_b_session_id,
        ]:
            session_key = self.storage._key(
                self.storage.key_config.session,
                user_id=self.user_id,
                session_id=sid,
            )
            raw = await self.storage._client.get(session_key)
            rec = SessionRecord.model_validate_json(raw)
            rec.team_id = self.team.id
            await self.storage._client.set(session_key, rec.model_dump_json())

    async def test_delete_team_cascades_workers_and_clears_leader(
        self,
    ) -> None:
        """delete_team removes all workers + sessions + clears leader."""
        result = await self.storage.delete_team(self.user_id, self.team.id)
        self.assertTrue(result)

        # Team record gone
        self.assertIsNone(
            await self.storage.get_team(self.user_id, self.team.id),
        )
        # Worker agents gone
        self.assertIsNone(
            await self.storage.get_agent(self.user_id, self.worker_a.id),
        )
        self.assertIsNone(
            await self.storage.get_agent(self.user_id, self.worker_b.id),
        )
        # Worker sessions gone
        self.assertIsNone(
            await self.storage.get_session(
                self.user_id,
                self.worker_a.id,
                self.worker_a_session_id,
            ),
        )
        # Leader session still exists, team_id cleared
        leader = await self.storage.get_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session_id,
        )
        self.assertIsNotNone(leader)
        self.assertIsNone(leader.team_id)

    async def test_delete_leader_session_dissolves_team(self) -> None:
        """Deleting a leader session auto-dissolves its team."""
        await self.storage.delete_session(
            self.user_id,
            self.leader_agent.id,
            self.leader_session_id,
        )

        # Team gone
        self.assertIsNone(
            await self.storage.get_team(self.user_id, self.team.id),
        )
        # Workers gone
        self.assertIsNone(
            await self.storage.get_agent(self.user_id, self.worker_a.id),
        )
        # Leader agent itself still exists (only the session was deleted)
        self.assertIsNotNone(
            await self.storage.get_agent(self.user_id, self.leader_agent.id),
        )

    async def test_delete_leader_agent_dissolves_all_its_teams(self) -> None:
        """Deleting the leader agent dissolves every team it leads."""
        await self.storage.delete_agent(self.user_id, self.leader_agent.id)

        self.assertIsNone(
            await self.storage.get_team(self.user_id, self.team.id),
        )
        self.assertIsNone(
            await self.storage.get_agent(self.user_id, self.worker_a.id),
        )
        self.assertIsNone(
            await self.storage.get_agent(self.user_id, self.leader_agent.id),
        )

    async def test_direct_delete_worker_agent_scrubs_member_ids(self) -> None:
        """Bypassing delete_team still keeps team.member_ids consistent."""
        await self.storage.delete_agent(self.user_id, self.worker_a.id)

        team = await self.storage.get_team(self.user_id, self.team.id)
        self.assertIsNotNone(team)
        self.assertEqual(team.data.member_ids, [self.worker_b.id])
        # The other worker is untouched
        self.assertIsNotNone(
            await self.storage.get_agent(self.user_id, self.worker_b.id),
        )

    async def test_direct_delete_worker_session_does_not_dissolve_team(
        self,
    ) -> None:
        """Deleting a worker's session leaves the team intact (asymmetric
        with leader-session deletion — there's no FK from session to its
        owning agent)."""
        await self.storage.delete_session(
            self.user_id,
            self.worker_a.id,
            self.worker_a_session_id,
        )

        # Team and worker agent still exist
        self.assertIsNotNone(
            await self.storage.get_team(self.user_id, self.team.id),
        )
        self.assertIsNotNone(
            await self.storage.get_agent(self.user_id, self.worker_a.id),
        )
        # The session is gone though
        self.assertIsNone(
            await self.storage.get_session(
                self.user_id,
                self.worker_a.id,
                self.worker_a_session_id,
            ),
        )

    async def test_delete_team_idempotent_on_nonexistent(self) -> None:
        """delete_team on a missing team returns False without crashing."""
        result = await self.storage.delete_team(self.user_id, "no-such-id")
        self.assertFalse(result)
