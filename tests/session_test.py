# -*- coding: utf-8 -*-
"""Session module tests."""
import os
from typing import Union
from unittest import IsolatedAsyncioTestCase

from agentscope.agent import ReActAgent, AgentBase
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.session import JSONSession
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

    async def asyncTearDown(self) -> None:
        """Clean up after the test."""
        # Remove the session file if it exists
        session_file = "./user_1.json"
        if os.path.exists(session_file):
            os.remove(session_file)
