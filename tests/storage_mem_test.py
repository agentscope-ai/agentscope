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
