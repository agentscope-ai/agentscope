# -*- coding: utf-8 -*-
"""Session module tests."""
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

from agentscope.agent import ReActAgent, AgentBase
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.session import JSONSession, RedisSession
from agentscope.tool import Toolkit


class MyAgent(AgentBase):
    """Test agent class."""

    def __init__(self) -> None:
        """Initialize the test agent."""
        super().__init__()
        self.name = "Friday"
        self.sys_prompt = "A helpful assistant."
        self.memory = InMemoryMemory()

        self.register_state("name")
        self.register_state("sys_prompt")

    async def reply(self, msg: Msg) -> None:
        """Reply to the message."""

    async def observe(self, msg: Msg) -> None:
        """Observe the message."""
        await self.memory.add(msg)

    async def handle_interrupt(
        self,
        msg: Union[Msg, list[Msg], None] = None,
    ) -> Msg:
        """Handle interrupt."""


class SessionTest(IsolatedAsyncioTestCase):
    """Test cases for the session module."""

    async def asyncSetUp(self) -> None:
        """Set up the test case."""
        session_file = "./user_1.json"
        if os.path.exists(session_file):
            os.remove(session_file)

    async def test_session_base(self) -> None:
        """Test the SessionBase class."""
        session = JSONSession(
            save_dir="./",
        )

        agent1 = ReActAgent(
            name="Friday",
            sys_prompt="A helpful assistant.",
            model=DashScopeChatModel(api_key="xxx", model_name="qwen_max"),
            formatter=DashScopeChatFormatter(),
            toolkit=Toolkit(),
            memory=InMemoryMemory(),
        )
        agent2 = MyAgent()

        await agent2.memory.add(
            Msg(
                "Alice",
                "Hi!",
                "user",
            ),
        )

        await session.save_session_state(
            session_id="user_1",
            agent1=agent1,
            agent2=agent2,
        )

        # Mutate local state to verify load really works
        agent1.name = "Changed"
        agent2.sys_prompt = "Changed prompt"

        # Load back
        await session.load_session_state(
            session_id="user_1",
            agent1=agent1,
            agent2=agent2,
        )

        self.assertEqual(agent1.name, "Friday")
        self.assertEqual(agent2.sys_prompt, "A helpful assistant.")

    async def asyncTearDown(self) -> None:
        """Clean up after the test."""
        # Remove the session file if it exists
        session_file = "./user_1.json"
        if os.path.exists(session_file):
            os.remove(session_file)


class RedisSessionTest(IsolatedAsyncioTestCase):
    """Test cases for the redis session module (with fake redis)."""

    async def asyncSetUp(self) -> None:
        # Use fakeredis (async)
        try:
            import fakeredis.aioredis  # type: ignore
        except ImportError as e:
            raise ImportError(
                "fakeredis is required for this test. "
                "Please install it via `pip install fakeredis`.",
            ) from e

        self._redis = fakeredis.aioredis.FakeRedis()
        self.session = RedisSession(
            connection_pool=self._redis.connection_pool,
        )

    async def test_redis_session_save_and_load(self) -> None:
        """Test the RedisSession class."""
        agent1 = ReActAgent(
            name="Friday",
            sys_prompt="A helpful assistant.",
            model=DashScopeChatModel(api_key="xxx", model_name="qwen_max"),
            formatter=DashScopeChatFormatter(),
            toolkit=Toolkit(),
            memory=InMemoryMemory(),
        )
        agent2 = MyAgent()

        await agent2.memory.add(Msg("Alice", "Hi!", "user"))

        # Save
        await self.session.save_session_state(
            session_id="user_1",
            agent1=agent1,
            agent2=agent2,
        )

        # Mutate local state to verify load really works
        agent1.name = "Changed"
        agent2.sys_prompt = "Changed prompt"

        # Load back
        await self.session.load_session_state(
            session_id="user_1",
            agent1=agent1,
            agent2=agent2,
        )

        self.assertEqual(agent1.name, "Friday")
        self.assertEqual(agent2.sys_prompt, "A helpful assistant.")

    async def asyncTearDown(self) -> None:
        # close clients
        await self.session.close()
        await self._redis.close()


@dataclass
class _FakeSession:
    """In-memory fake of tablestore_for_agent_memory Session dataclass."""

    user_id: str
    session_id: str
    update_time: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = field(default_factory=dict)


class _FakeMemoryStore:
    """In-memory fake of AsyncMemoryStore for testing."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], _FakeSession] = {}

    async def init_table(self) -> None:
        """Initialize the fake memory store."""
        pass  # pylint: disable=unnecessary-pass

    async def init_search_index(self) -> None:
        """Initialize the fake memory store."""
        pass  # pylint: disable=unnecessary-pass

    async def update_session(self, session: _FakeSession) -> None:
        """Update a session."""
        key = (session.user_id, session.session_id)
        existing = self._sessions.get(key)
        if existing:
            if session.metadata:
                existing.metadata.update(session.metadata)
        else:
            self._sessions[key] = _FakeSession(
                user_id=session.user_id,
                session_id=session.session_id,
                metadata=dict(session.metadata) if session.metadata else {},
            )

    async def get_session(
        self,
        user_id: str,
        session_id: str,
    ) -> Optional[_FakeSession]:
        """Get a session."""
        return self._sessions.get((user_id, session_id))

    async def close(self) -> None:
        """Close the fake memory store."""
        self._sessions.clear()


def _build_tablestore_mocks() -> tuple[MagicMock, MagicMock]:
    """Create mock modules for tablestore and tablestore_for_agent_memory."""
    tablestore_mod = MagicMock()
    tablestore_mod.AsyncOTSClient = MagicMock
    tablestore_mod.WriteRetryPolicy = MagicMock

    memory_base_mod = MagicMock()
    memory_base_mod.Session = _FakeSession

    memory_store_mod = MagicMock()

    agent_memory_mod = MagicMock()
    agent_memory_mod.base = MagicMock()
    agent_memory_mod.base.base_memory_store = memory_base_mod
    agent_memory_mod.memory = MagicMock()
    agent_memory_mod.memory.async_memory_store = memory_store_mod

    return tablestore_mod, agent_memory_mod


class TablestoreSessionTest(
    IsolatedAsyncioTestCase,
):  # pylint: disable=protected-access
    """Test cases for the TablestoreSession module (with mocked backend)."""

    async def asyncSetUp(self) -> None:
        """Set up mock modules and create a TablestoreSession instance."""
        (
            self._tablestore_mod,
            self._agent_memory_mod,
        ) = _build_tablestore_mocks()
        self._fake_store = _FakeMemoryStore()

        self._agent_memory_mod.memory.async_memory_store.AsyncMemoryStore = (
            MagicMock(return_value=self._fake_store)
        )

        self._patches = [
            patch.dict(
                sys.modules,
                {
                    "tablestore": self._tablestore_mod,
                    "tablestore_for_agent_memory": self._agent_memory_mod,
                    "tablestore_for_agent_memory.base": (
                        self._agent_memory_mod.base
                    ),
                    "tablestore_for_agent_memory.base.base_memory_store": (
                        self._agent_memory_mod.base.base_memory_store
                    ),
                    "tablestore_for_agent_memory.memory": (
                        self._agent_memory_mod.memory
                    ),
                    "tablestore_for_agent_memory.memory.async_memory_store": (
                        self._agent_memory_mod.memory.async_memory_store
                    ),
                },
            ),
        ]
        for patcher in self._patches:
            patcher.start()

        from agentscope.session._tablestore_session import TablestoreSession

        self.session = TablestoreSession(
            end_point="https://fake.endpoint.com",
            instance_name="fake_instance",
            access_key_id="fake_ak",
            access_key_secret="fake_sk",
        )
        self.session._memory_store = self._fake_store
        self.session._initialized = True

    async def test_save_and_load_session_state(self) -> None:
        """Test saving and loading session state round-trip."""
        agent = MyAgent()
        await agent.memory.add(Msg("Alice", "Hi!", "user"))

        await self.session.save_session_state(
            session_id="sess_1",
            user_id="user_1",
            agent=agent,
        )

        agent.name = "Changed"
        agent.sys_prompt = "Changed prompt"

        await self.session.load_session_state(
            session_id="sess_1",
            user_id="user_1",
            agent=agent,
        )

        self.assertEqual(agent.name, "Friday")
        self.assertEqual(agent.sys_prompt, "A helpful assistant.")

    async def test_load_before_save_session_not_exist(self) -> None:
        """Test that load before any save reports session not found."""
        agent = MyAgent()

        with self.assertLogs("as", level="INFO") as log_context:
            await self.session.load_session_state(
                session_id="nonexistent",
                user_id="user_1",
                allow_not_exist=True,
                agent=agent,
            )

        self.assertTrue(
            any("does not exist" in msg for msg in log_context.output),
        )

    async def test_load_before_save_raises_when_not_allowed(self) -> None:
        """Test that load raises ValueError when allow_not_exist=False."""
        agent = MyAgent()

        with self.assertRaises(ValueError):
            await self.session.load_session_state(
                session_id="nonexistent",
                user_id="user_1",
                allow_not_exist=False,
                agent=agent,
            )

    async def test_lifecycle_load_save_load(self) -> None:
        """Test full lifecycle: load (not exist) -> save -> load (exists)."""
        agent = MyAgent()
        original_name = agent.name
        original_prompt = agent.sys_prompt

        with self.assertLogs("as", level="INFO") as log_before:
            await self.session.load_session_state(
                session_id="lifecycle_sess",
                user_id="user_1",
                allow_not_exist=True,
                agent=agent,
            )

        self.assertTrue(
            any("does not exist" in msg for msg in log_before.output),
        )
        self.assertEqual(agent.name, original_name)
        self.assertEqual(agent.sys_prompt, original_prompt)

        await agent.memory.add(Msg("Bob", "Hello!", "user"))
        await self.session.save_session_state(
            session_id="lifecycle_sess",
            user_id="user_1",
            agent=agent,
        )

        agent.name = "Mutated"
        agent.sys_prompt = "Mutated prompt"

        await self.session.load_session_state(
            session_id="lifecycle_sess",
            user_id="user_1",
            agent=agent,
        )

        self.assertEqual(agent.name, original_name)
        self.assertEqual(agent.sys_prompt, original_prompt)

    async def test_save_overwrites_previous_state(self) -> None:
        """Test that saving again overwrites the previous state."""
        agent = MyAgent()

        await self.session.save_session_state(
            session_id="overwrite_sess",
            user_id="user_1",
            agent=agent,
        )

        agent.name = "UpdatedName"
        agent.sys_prompt = "Updated prompt"

        await self.session.save_session_state(
            session_id="overwrite_sess",
            user_id="user_1",
            agent=agent,
        )

        agent.name = "Garbage"
        agent.sys_prompt = "Garbage"

        await self.session.load_session_state(
            session_id="overwrite_sess",
            user_id="user_1",
            agent=agent,
        )

        self.assertEqual(agent.name, "UpdatedName")
        self.assertEqual(agent.sys_prompt, "Updated prompt")

    async def test_close(self) -> None:
        """Test that close resets the session state."""
        await self.session.close()
        self.assertIsNone(self.session._memory_store)
        self.assertFalse(self.session._initialized)

    async def test_async_context_manager(self) -> None:
        """Test the async context manager protocol."""
        self.session._initialized = False
        self.session._memory_store = None

        self._agent_memory_mod.memory.async_memory_store.AsyncMemoryStore = (
            MagicMock(return_value=self._fake_store)
        )

        async with self.session as session:
            self.assertIs(session, self.session)
            self.assertTrue(session._initialized)

        self.assertIsNone(self.session._memory_store)
        self.assertFalse(self.session._initialized)

    async def asyncTearDown(self) -> None:
        """Clean up patches."""
        for patcher in self._patches:
            patcher.stop()
