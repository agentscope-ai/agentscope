# -*- coding: utf-8 -*-
"""Session module tests."""
import os
import tempfile
from typing import Union
from unittest import IsolatedAsyncioTestCase, TestCase

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


class JSONSessionPathSecurityTest(TestCase):
    """Security test cases for JSONSession path handling."""

    def test_get_save_path_stays_under_save_dir(self) -> None:
        """Path traversal payloads must not escape the save directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session = JSONSession(save_dir=temp_dir)
            save_path = session._get_save_path(
                session_id="sid",
                user_id="../../../../tmp/evil",
            )
            expected_dir = os.path.realpath(temp_dir)
            self.assertEqual(
                os.path.dirname(save_path),
                expected_dir,
            )
            self.assertNotIn("/", os.path.basename(save_path))
            self.assertNotIn("\\", os.path.basename(save_path))

    def test_save_and_load_use_sanitized_identifiers(self) -> None:
        """Save/load should use the same sanitized path deterministically."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session = JSONSession(save_dir=temp_dir)
            save_path = session._get_save_path(
                session_id="session/1",
                user_id="user/1",
            )
            self.assertTrue(
                os.path.exists(os.path.dirname(save_path)),
            )
            self.assertEqual(
                os.path.basename(save_path),
                "user_1_session_1.json",
            )

    def test_empty_session_id_raises(self) -> None:
        """An empty session id should be rejected."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session = JSONSession(save_dir=temp_dir)
            with self.assertRaises(ValueError):
                session._get_save_path(session_id="", user_id="user")


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
