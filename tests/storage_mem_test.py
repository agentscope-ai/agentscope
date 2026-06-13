# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for MemStorage."""

from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.app.storage import (
    MemStorage,
    AgentRecord,
    SessionConfig,
    ChatModelConfig,
    ScheduleRecord,
    ScheduleData,
    SessionSource,
    TeamData,
    TeamRecord,
)
from agentscope.app.storage import AgentData
from agentscope.credential import OllamaCredential
from agentscope.agent import ContextConfig, ReActConfig
from agentscope.message import UserMsg, AssistantMsg, TextBlock
from agentscope.state import AgentState


def make_storage() -> MemStorage:
    """Create a MemStorage instance."""
    return MemStorage()


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


class TestCredential(IsolatedAsyncioTestCase):
    """Tests for credential CRUD."""

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
        """Delete a credential and verify it is gone."""
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
    """Tests for agent CRUD."""

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
        """Delete an agent and verify it is gone."""
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
    """Tests for session CRUD."""

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

    async def test_delete(self) -> None:
        """Delete a session and verify it is gone."""
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
    """Tests for message persistence."""

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

    async def test_message_isolation_via_model_copy(self) -> None:
        """Modifying the original message after upsert does not affect
        the stored copy."""
        msg = UserMsg(name="alice", content="original")
        await self.storage.upsert_message(self.user_id, self.session_id, msg)

        msg.content = [TextBlock(text="modified")]

        stored = await self.storage.get_message(
            self.user_id,
            self.session_id,
            msg.id,
        )
        self.assertIsNotNone(stored)
        self.assertEqual(stored.content[0].text, "original")


class TestSchedule(IsolatedAsyncioTestCase):
    """Tests for schedule CRUD."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"

    async def test_create(self) -> None:
        """Create a schedule and verify it is retrievable via list."""
        record = make_schedule_record(self.user_id, self.agent_id)
        schedule_id = await self.storage.upsert_schedule(self.user_id, record)
        records = await self.storage.list_schedules(self.user_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, schedule_id)

    async def test_list_empty(self) -> None:
        """Verify list returns empty when no records exist."""
        records = await self.storage.list_schedules(self.user_id)
        self.assertEqual(records, [])

    async def test_delete(self) -> None:
        """Delete a schedule and verify it is gone."""
        record = make_schedule_record(self.user_id, self.agent_id)
        await self.storage.upsert_schedule(self.user_id, record)
        result = await self.storage.delete_schedule(self.user_id, record.id)
        self.assertTrue(result)
        records = await self.storage.list_schedules(self.user_id)
        self.assertEqual(records, [])

    async def test_delete_nonexistent(self) -> None:
        """Verify delete returns False for non-existent record."""
        result = await self.storage.delete_schedule(self.user_id, "no-such-id")
        self.assertFalse(result)


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

    async def test_delete(self) -> None:
        """Delete a team and verify it is gone."""
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


class TestCascade(IsolatedAsyncioTestCase):
    """Tests for cascade deletion behavior."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"

    async def test_delete_agent_cascades_sessions(self) -> None:
        """Deleting an agent cascades to its sessions."""
        record = make_agent_record(self.user_id)
        await self.storage.upsert_agent(self.user_id, record)
        await self.storage.upsert_session(
            self.user_id,
            record.id,
            make_session_config(),
        )

        await self.storage.delete_agent(self.user_id, record.id)

        sessions = await self.storage.list_sessions(self.user_id, record.id)
        self.assertEqual(sessions, [])

    async def test_delete_schedule_cascades_sessions(self) -> None:
        """Deleting a schedule cascades to its execution sessions."""
        schedule = make_schedule_record(self.user_id, self.agent_id)
        await self.storage.upsert_schedule(self.user_id, schedule)

        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
            source=SessionSource.SCHEDULE,
            source_schedule_id=schedule.id,
        )

        await self.storage.delete_schedule(self.user_id, schedule.id)

        sessions = await self.storage.list_sessions_by_schedule(
            self.user_id,
            schedule.id,
        )
        self.assertEqual(sessions, [])

    async def test_delete_team_cascades_workers(self) -> None:
        """Deleting a team cascades to its worker agents."""
        leader_record = make_agent_record(self.user_id)
        await self.storage.upsert_agent(self.user_id, leader_record)

        leader_session = await self.storage.upsert_session(
            self.user_id,
            leader_record.id,
            make_session_config(),
        )

        worker_a = AgentRecord(
            id="worker-a",
            user_id=self.user_id,
            source="team",
            data=AgentData(
                id="data-a",
                name="worker-a",
                system_prompt="worker",
                context_config=ContextConfig(),
                react_config=ReActConfig(),
            ),
        )
        await self.storage.upsert_agent(self.user_id, worker_a)

        team = make_team_record(
            self.user_id,
            session_id=leader_session.id,
            member_ids=[worker_a.id],
        )
        await self.storage.upsert_team(self.user_id, team)

        await self.storage.delete_team(self.user_id, team.id)

        self.assertIsNone(
            await self.storage.get_team(self.user_id, team.id),
        )
        self.assertIsNone(
            await self.storage.get_agent(self.user_id, worker_a.id),
        )


class TestSetSessionTeamId(IsolatedAsyncioTestCase):
    """Tests for set_session_team_id."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"

    async def test_set_team_id(self) -> None:
        """set_session_team_id sets the team_id on a session."""
        session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
        )

        await self.storage.set_session_team_id(
            self.user_id,
            session.id,
            "team-1",
        )

        loaded = await self.storage.get_session(
            self.user_id,
            self.agent_id,
            session.id,
        )
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.team_id, "team-1")

    async def test_clear_team_id(self) -> None:
        """set_session_team_id with None clears the team_id."""
        session = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
        )
        await self.storage.set_session_team_id(
            self.user_id,
            session.id,
            "team-1",
        )

        await self.storage.set_session_team_id(
            self.user_id,
            session.id,
            None,
        )

        loaded = await self.storage.get_session(
            self.user_id,
            self.agent_id,
            session.id,
        )
        self.assertIsNotNone(loaded)
        self.assertIsNone(loaded.team_id)

    async def test_set_team_id_nonexistent_session_noop(self) -> None:
        """set_session_team_id on a non-existent session is a no-op."""
        await self.storage.set_session_team_id(
            self.user_id,
            "no-such-session",
            "team-1",
        )


class TestReadIsolation(IsolatedAsyncioTestCase):
    """Regression tests for read-side isolation guarantees."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"
        self.session_id = "session-1"

    async def test_get_message_returns_deep_copy(self) -> None:
        """Mutating a Msg returned by get_message must not affect storage."""
        msg = UserMsg(name="alice", content="original")
        await self.storage.upsert_message(self.user_id, self.session_id, msg)

        first_read = await self.storage.get_message(
            self.user_id,
            self.session_id,
            msg.id,
        )
        self.assertIsNotNone(first_read)
        first_read.content = [TextBlock(text="hijacked")]

        second_read = await self.storage.get_message(
            self.user_id,
            self.session_id,
            msg.id,
        )
        self.assertIsNotNone(second_read)
        self.assertEqual(second_read.content[0].text, "original")

    async def test_list_messages_returns_deep_copies(self) -> None:
        """Mutating items returned by list_messages must not affect storage."""
        original = UserMsg(name="alice", content="original")
        await self.storage.upsert_message(
            self.user_id,
            self.session_id,
            original,
        )

        first = await self.storage.list_messages(
            self.user_id,
            self.session_id,
        )
        first[0].content = [TextBlock(text="hijacked")]

        second = await self.storage.list_messages(
            self.user_id,
            self.session_id,
        )
        self.assertEqual(second[0].content[0].text, "original")


class TestNoGhostUserOnRead(IsolatedAsyncioTestCase):
    """Regression tests: read paths must not auto-create user nodes."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.unknown_user = "never-seen-this-user"

    def assert_no_ghost_user(self) -> None:
        """Assert no internal state was created for an unknown user."""
        self.assertNotIn(self.unknown_user, self.storage._records)
        self.assertNotIn(self.unknown_user, self.storage._indexes)
        self.assertNotIn(self.unknown_user, self.storage._messages)

    async def test_get_credential_unknown_user(self) -> None:
        """get_credential for an unknown user must not create a user node."""
        result = await self.storage.get_credential(
            self.unknown_user,
            "any-id",
        )
        self.assertIsNone(result)
        self.assert_no_ghost_user()

    async def test_list_credentials_unknown_user(self) -> None:
        """list_credentials for an unknown user must not create a user node."""
        result = await self.storage.list_credentials(self.unknown_user)
        self.assertEqual(result, [])
        self.assert_no_ghost_user()

    async def test_delete_credential_unknown_user(self) -> None:
        """delete_credential for an unknown user must not create a user node."""
        result = await self.storage.delete_credential(
            self.unknown_user,
            "any-id",
        )
        self.assertFalse(result)
        self.assert_no_ghost_user()

    async def test_get_agent_unknown_user(self) -> None:
        """get_agent for an unknown user must not create a user node."""
        result = await self.storage.get_agent(self.unknown_user, "any-id")
        self.assertIsNone(result)
        self.assert_no_ghost_user()

    async def test_list_agents_unknown_user(self) -> None:
        """list_agents for an unknown user must not create a user node."""
        result = await self.storage.list_agents(self.unknown_user)
        self.assertEqual(result, [])
        self.assert_no_ghost_user()

    async def test_delete_agent_unknown_user(self) -> None:
        """delete_agent for an unknown user must not create a user node."""
        result = await self.storage.delete_agent(self.unknown_user, "any-id")
        self.assertFalse(result)
        self.assert_no_ghost_user()

    async def test_get_session_unknown_user(self) -> None:
        """get_session for an unknown user must not create a user node."""
        result = await self.storage.get_session(
            self.unknown_user,
            "any-agent",
            "any-id",
        )
        self.assertIsNone(result)
        self.assert_no_ghost_user()

    async def test_list_sessions_unknown_user(self) -> None:
        """list_sessions for an unknown user must not create a user node."""
        result = await self.storage.list_sessions(
            self.unknown_user,
            "any-agent",
        )
        self.assertEqual(result, [])
        self.assert_no_ghost_user()

    async def test_delete_session_unknown_user(self) -> None:
        """delete_session for an unknown user must not create a user node."""
        result = await self.storage.delete_session(
            self.unknown_user,
            "any-agent",
            "any-id",
        )
        self.assertFalse(result)
        self.assert_no_ghost_user()

    async def test_list_sessions_by_schedule_unknown_user(self) -> None:
        """list_sessions_by_schedule for unknown user creates no user node."""
        result = await self.storage.list_sessions_by_schedule(
            self.unknown_user,
            "any-schedule",
        )
        self.assertEqual(result, [])
        self.assert_no_ghost_user()

    async def test_get_schedule_unknown_user(self) -> None:
        """get_schedule for an unknown user must not create a user node."""
        result = await self.storage.get_schedule(
            self.unknown_user,
            "any-id",
        )
        self.assertIsNone(result)
        self.assert_no_ghost_user()

    async def test_list_schedules_unknown_user(self) -> None:
        """list_schedules for an unknown user must not create a user node."""
        result = await self.storage.list_schedules(self.unknown_user)
        self.assertEqual(result, [])
        self.assert_no_ghost_user()

    async def test_delete_schedule_unknown_user(self) -> None:
        """delete_schedule for an unknown user must not create a user node."""
        result = await self.storage.delete_schedule(
            self.unknown_user,
            "any-id",
        )
        self.assertFalse(result)
        self.assert_no_ghost_user()

    async def test_get_team_unknown_user(self) -> None:
        """get_team for an unknown user must not create a user node."""
        result = await self.storage.get_team(self.unknown_user, "any-id")
        self.assertIsNone(result)
        self.assert_no_ghost_user()

    async def test_list_teams_unknown_user(self) -> None:
        """list_teams for an unknown user must not create a user node."""
        result = await self.storage.list_teams(self.unknown_user)
        self.assertEqual(result, [])
        self.assert_no_ghost_user()

    async def test_delete_team_unknown_user(self) -> None:
        """delete_team for an unknown user must not create a user node."""
        result = await self.storage.delete_team(self.unknown_user, "any-id")
        self.assertFalse(result)
        self.assert_no_ghost_user()

    async def test_set_session_team_id_unknown_user(self) -> None:
        """set_session_team_id for an unknown user must not create a node."""
        await self.storage.set_session_team_id(
            self.unknown_user,
            "any-session",
            "team-1",
        )
        self.assert_no_ghost_user()

    async def test_get_message_unknown_user(self) -> None:
        """get_message for an unknown user must not create a user node."""
        result = await self.storage.get_message(
            self.unknown_user,
            "any-session",
            "any-msg",
        )
        self.assertIsNone(result)
        self.assert_no_ghost_user()

    async def test_list_messages_unknown_user(self) -> None:
        """list_messages for an unknown user must not create a user node."""
        result = await self.storage.list_messages(
            self.unknown_user,
            "any-session",
        )
        self.assertEqual(result, [])
        self.assert_no_ghost_user()

    async def test_update_session_state_unknown_user_raises(self) -> None:
        """update_session_state on unknown user raises and creates no node."""
        with self.assertRaises(KeyError):
            await self.storage.update_session_state(
                self.unknown_user,
                "any-agent",
                "any-session",
                AgentState(),
            )
        self.assert_no_ghost_user()


class TestDeleteSessionMessagesIsolation(IsolatedAsyncioTestCase):
    """Regression: delete_session must not create a _messages ghost node."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"
        self.agent_id = "agent-1"

    async def test_delete_session_without_messages_no_ghost(self) -> None:
        """A session that never received messages leaves _messages clean."""
        record = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
        )
        self.assertNotIn(self.user_id, self.storage._messages)

        deleted = await self.storage.delete_session(
            self.user_id,
            self.agent_id,
            record.id,
        )
        self.assertTrue(deleted)
        self.assertNotIn(self.user_id, self.storage._messages)

    async def test_delete_session_with_messages_clears_entry(self) -> None:
        """A session that did receive messages has its entry removed."""
        record = await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            make_session_config(),
        )
        msg = UserMsg(name="alice", content="hi")
        await self.storage.upsert_message(self.user_id, record.id, msg)
        self.assertIn(self.user_id, self.storage._messages)

        deleted = await self.storage.delete_session(
            self.user_id,
            self.agent_id,
            record.id,
        )
        self.assertTrue(deleted)
        msg_key = self.storage._message_key(self.user_id, record.id)
        self.assertNotIn(msg_key, self.storage._messages[self.user_id])


class TestAcloseDoesNotWipeData(IsolatedAsyncioTestCase):
    """Regression: aclose() must preserve in-memory data (Redis-aligned)."""

    async def test_aclose_keeps_records(self) -> None:
        """Calling aclose() does not erase records — they live until GC."""
        storage = make_storage()
        user_id = "user-1"
        cred_id = await storage.upsert_credential(
            user_id,
            OllamaCredential(name="ollama-1"),
        )

        await storage.aclose()

        record = await storage.get_credential(user_id, cred_id)
        self.assertIsNotNone(record)

    async def test_async_with_keeps_records_after_exit(self) -> None:
        """Data survives ``async with`` exit (mirrors RedisStorage)."""
        storage = make_storage()
        user_id = "user-1"
        async with storage:
            cred_id = await storage.upsert_credential(
                user_id,
                OllamaCredential(name="ollama-1"),
            )

        record = await storage.get_credential(user_id, cred_id)
        self.assertIsNotNone(record)


class TestIndexCleanupOnDiscard(IsolatedAsyncioTestCase):
    """Regression: discard must clean up empty index sets and user dicts."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"

    async def test_delete_credential_drops_empty_index_set(self) -> None:
        """After deleting the only credential, its index set is removed."""
        cred_id = await self.storage.upsert_credential(
            self.user_id,
            OllamaCredential(name="ollama-1"),
        )
        index_name = self.storage._index_name("credential")
        self.assertIn(index_name, self.storage._indexes[self.user_id])

        deleted = await self.storage.delete_credential(self.user_id, cred_id)
        self.assertTrue(deleted)
        self.assertNotIn(self.user_id, self.storage._indexes)

    async def test_delete_agent_drops_empty_index_set(self) -> None:
        """After deleting the only agent, its index set is removed."""
        agent = make_agent_record(self.user_id)
        await self.storage.upsert_agent(self.user_id, agent)
        deleted = await self.storage.delete_agent(self.user_id, agent.id)
        self.assertTrue(deleted)
        self.assertNotIn(self.user_id, self.storage._indexes)

    async def test_delete_session_drops_empty_session_index(self) -> None:
        """After deleting the only session, both session indexes are gone."""
        agent_id = "agent-1"
        record = await self.storage.upsert_session(
            self.user_id,
            agent_id,
            make_session_config(),
        )
        session_idx_key = self.storage._session_index_key(
            self.user_id,
            agent_id,
        )
        index_name = self.storage._index_name("session")
        self.assertIn(session_idx_key, self.storage._indexes[self.user_id])
        self.assertIn(index_name, self.storage._indexes[self.user_id])

        deleted = await self.storage.delete_session(
            self.user_id,
            agent_id,
            record.id,
        )
        self.assertTrue(deleted)
        self.assertNotIn(self.user_id, self.storage._indexes)

    async def test_delete_team_drops_empty_index_set(self) -> None:
        """After deleting the only team, its index set is removed."""
        team = make_team_record(self.user_id)
        await self.storage.upsert_team(self.user_id, team)
        deleted = await self.storage.delete_team(self.user_id, team.id)
        self.assertTrue(deleted)
        self.assertNotIn(self.user_id, self.storage._indexes)

    async def test_delete_schedule_drops_empty_indexes(self) -> None:
        """delete_schedule reclaims user index dict and global index entry."""
        record = make_schedule_record(self.user_id, "agent-1")
        await self.storage.upsert_schedule(self.user_id, record)

        deleted = await self.storage.delete_schedule(self.user_id, record.id)
        self.assertTrue(deleted)
        self.assertNotIn(self.user_id, self.storage._indexes)
        self.assertEqual(
            self.storage._indexes.get("__global__", {}).get(
                "schedule_global_ids",
                set(),
            ),
            set(),
        )

    async def test_partial_delete_keeps_index_set(self) -> None:
        """Deleting one of two records keeps the index set populated."""
        cred_id_1 = await self.storage.upsert_credential(
            self.user_id,
            OllamaCredential(name="ollama-1"),
        )
        await self.storage.upsert_credential(
            self.user_id,
            OllamaCredential(name="ollama-2"),
        )

        await self.storage.delete_credential(self.user_id, cred_id_1)
        index_name = self.storage._index_name("credential")
        self.assertEqual(
            len(self.storage._indexes[self.user_id][index_name]),
            1,
        )
