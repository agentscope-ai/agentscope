# -*- coding: utf-8 -*-
"""Unit tests for RedisStorage using fakeredis."""
from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.app.storage import (
    RedisStorage,
    RedisKeyConfig,
    AgentRecord,
    SessionConfig,
    ChatModelConfig,
    ScheduleRecord,
    ScheduleData,
    SessionSource,
)
from agentscope.credential import OllamaCredential
from agentscope.app.storage import AgentData
from agentscope.agent import ContextConfig, ReActConfig
from agentscope.message import UserMsg, AssistantMsg, TextBlock


def make_storage() -> RedisStorage:
    """Create a RedisStorage instance backed by fakeredis."""
    storage = RedisStorage.__new__(RedisStorage)
    # pylint: disable=protected-access
    storage._client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage.key_ttl = None
    storage.key_config = RedisKeyConfig()
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
