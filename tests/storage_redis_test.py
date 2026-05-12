# -*- coding: utf-8 -*-
"""Unit tests for RedisStorage using fakeredis."""
from unittest.async_case import IsolatedAsyncioTestCase

import fakeredis.aioredis

from agentscope.app.storage._redis_storage import RedisStorage, RedisKeyConfig
from agentscope.app.storage._model import (
    AgentRecord,
    CredentialBase,
    WorkspaceBase,
    SessionData,
)
from agentscope.app.storage._model._agent import AgentData
from agentscope.app.storage._model._session import ChatModelConfig
from agentscope.agent import ContextConfig, ReActConfig
from agentscope.state import AgentState


def make_storage() -> RedisStorage:
    """Create a RedisStorage instance backed by fakeredis."""
    storage = RedisStorage.__new__(RedisStorage)
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


def make_session_data() -> SessionData:
    """Create a test SessionData with all-default agent state."""
    return SessionData(
        agent_state=AgentState(),
        chat_model_config=ChatModelConfig(
            type="openai",
            credential_id="cred-1",
            parameters={"model": "gpt-4"},
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
            CredentialBase(data={"key": "value"}),
        )
        records = await self.storage.list_credentials(self.user_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, cred_id)
        self.assertEqual(records[0].data, {"key": "value"})

    async def test_list_empty(self) -> None:
        """Verify list returns empty when no records exist."""
        records = await self.storage.list_credentials(self.user_id)
        self.assertEqual(records, [])

    async def test_update_in_place(self) -> None:
        """Update a credential and verify data changed without adding a new record."""
        cred_id = await self.storage.upsert_credential(
            self.user_id,
            CredentialBase(data={"key": "old"}),
        )
        await self.storage.upsert_credential(
            self.user_id,
            CredentialBase(id=cred_id, data={"key": "new"}),
        )
        records = await self.storage.list_credentials(self.user_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].data, {"key": "new"})

    async def test_delete(self) -> None:
        """Delete a credential and verify it is gone from Redis."""
        cred_id = await self.storage.upsert_credential(
            self.user_id,
            CredentialBase(data={"key": "value"}),
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
            CredentialBase(data={"a": 1}),
        )
        records = await self.storage.list_credentials("user-B")
        self.assertEqual(records, [])


class TestWorkspace(IsolatedAsyncioTestCase):
    """Tests for workspace CRUD and cascading operations."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.storage = make_storage()
        self.user_id = "user-1"

    async def test_create(self) -> None:
        """Create a workspace and verify it is retrievable via list."""
        ws_id = await self.storage.upsert_workspace(
            self.user_id,
            WorkspaceBase(agent_id="agent-1", data={"env": "dev"}),
        )
        records = await self.storage.list_workspaces(self.user_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, ws_id)
        self.assertEqual(records[0].data, {"env": "dev"})

    async def test_list_empty(self) -> None:
        """Verify list returns empty when no records exist."""
        records = await self.storage.list_workspaces(self.user_id)
        self.assertEqual(records, [])

    async def test_update_in_place(self) -> None:
        """Update a workspace and verify data changed without adding a new record."""
        ws_id = await self.storage.upsert_workspace(
            self.user_id,
            WorkspaceBase(agent_id="agent-1", data={"env": "dev"}),
        )
        await self.storage.upsert_workspace(
            self.user_id,
            WorkspaceBase(id=ws_id, agent_id="agent-1", data={"env": "prod"}),
        )
        records = await self.storage.list_workspaces(self.user_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].data, {"env": "prod"})

    async def test_delete(self) -> None:
        """Delete a workspace and verify it is gone from Redis."""
        ws_id = await self.storage.upsert_workspace(
            self.user_id,
            WorkspaceBase(agent_id="agent-1", data={}),
        )
        result = await self.storage.delete_workspace(self.user_id, ws_id)
        self.assertTrue(result)
        records = await self.storage.list_workspaces(self.user_id)
        self.assertEqual(records, [])

    async def test_delete_nonexistent(self) -> None:
        """Verify delete returns False for non-existent record."""
        result = await self.storage.delete_workspace(
            self.user_id,
            "no-such-id",
        )
        self.assertFalse(result)

    async def test_user_isolation(self) -> None:
        """Verify different users cannot see each other's records."""
        await self.storage.upsert_workspace(
            "user-A",
            WorkspaceBase(agent_id="agent-1", data={}),
        )
        records = await self.storage.list_workspaces("user-B")
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
        agent_id = await self.storage.create_agent(self.user_id, record)
        records = await self.storage.list_agent(self.user_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, agent_id)
        self.assertEqual(records[0].data.name, "test-agent")

    async def test_list_empty(self) -> None:
        """Verify list returns empty when no records exist."""
        records = await self.storage.list_agent(self.user_id)
        self.assertEqual(records, [])

    async def test_delete(self) -> None:
        """Delete an agent and verify it is gone from Redis."""
        record = make_agent_record(self.user_id)
        await self.storage.create_agent(self.user_id, record)
        result = await self.storage.delete_agent(self.user_id, record.id)
        self.assertTrue(result)
        records = await self.storage.list_agent(self.user_id)
        self.assertEqual(records, [])

    async def test_delete_nonexistent(self) -> None:
        """Verify delete returns False for non-existent record."""
        result = await self.storage.delete_agent(self.user_id, "no-such-id")
        self.assertFalse(result)

    async def test_user_isolation(self) -> None:
        """Verify different users cannot see each other's records."""
        await self.storage.create_agent("user-A", make_agent_record("user-A"))
        records = await self.storage.list_agent("user-B")
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
            self.workspace_id,
            make_session_data(),
        )
        records = await self.storage.list_sessions(self.user_id, self.agent_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].workspace_id, self.workspace_id)
        self.assertEqual(records[0].agent_id, self.agent_id)

    async def test_list_empty(self) -> None:
        """Verify list returns empty when no records exist."""
        records = await self.storage.list_sessions(self.user_id, self.agent_id)
        self.assertEqual(records, [])

    async def test_upsert_same_triple_updates_in_place(self) -> None:
        """Second upsert for the same (user, agent, workspace) must update the
        existing record, not create a second one."""
        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            self.workspace_id,
            make_session_data(),
        )
        records_before = await self.storage.list_sessions(
            self.user_id,
            self.agent_id,
        )
        first_id = records_before[0].id

        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            self.workspace_id,
            make_session_data(),
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
            self.workspace_id,
            make_session_data(),
        )
        records = await self.storage.list_sessions(self.user_id, self.agent_id)
        result = await self.storage.delete_session(self.user_id, records[0].id)
        self.assertTrue(result)
        remaining = await self.storage.list_sessions(
            self.user_id,
            self.agent_id,
        )
        self.assertEqual(remaining, [])

    async def test_delete_cascades_lookup_key(self) -> None:
        """Deleting a session must remove the lookup key so a subsequent upsert
        for the same triple creates a fresh session with a new id."""
        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            self.workspace_id,
            make_session_data(),
        )
        records = await self.storage.list_sessions(self.user_id, self.agent_id)
        old_id = records[0].id

        await self.storage.delete_session(self.user_id, old_id)

        await self.storage.upsert_session(
            self.user_id,
            self.agent_id,
            self.workspace_id,
            make_session_data(),
        )
        new_records = await self.storage.list_sessions(
            self.user_id,
            self.agent_id,
        )
        self.assertEqual(len(new_records), 1)
        self.assertNotEqual(new_records[0].id, old_id)

    async def test_delete_nonexistent(self) -> None:
        """Verify delete returns False for non-existent record."""
        result = await self.storage.delete_session(self.user_id, "no-such-id")
        self.assertFalse(result)

    async def test_agent_isolation(self) -> None:
        """Verify different agents cannot see each other's sessions."""
        await self.storage.upsert_session(
            self.user_id,
            "agent-A",
            self.workspace_id,
            make_session_data(),
        )
        records = await self.storage.list_sessions(self.user_id, "agent-B")
        self.assertEqual(records, [])
