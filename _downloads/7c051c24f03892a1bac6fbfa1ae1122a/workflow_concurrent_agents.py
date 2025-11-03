# -*- coding: utf-8 -*-
"""
Concurrent Agents
===================================
With the help of asynchronous programming, the concurrent agents can be executed by ``asyncio.gather`` in Python.

A simple example is shown below, where two agents are created and executed concurrently.
"""
import asyncio
from datetime import datetime
from typing import Any

from agentscope.agent import AgentBase


class ExampleAgent(AgentBase):
    """The example agent for concurrent execution."""

    def __init__(self, name: str) -> None:
        """Initialize the agent with its name."""
        super().__init__()
        self.name = name

    async def reply(self, *args: Any, **kwargs: Any) -> None:
        """Reply to the message."""
        start_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"{self.name} started at {start_time}")
        await asyncio.sleep(3)  # Simulate a long-running task
        end_time = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        print(f"{self.name} finished at {end_time}")


async def run_concurrent_agents() -> None:
    """Run the concurrent agents."""
    agent1 = ExampleAgent("Agent 1")
    agent2 = ExampleAgent("Agent 2")

    await asyncio.gather(agent1(), agent2())


asyncio.run(run_concurrent_agents())
