# -*- coding: utf-8 -*-
"""Example for gather the printing messages from multiple agents."""
import asyncio
import os

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeMultiAgentFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.pipeline import MsgHub, stream_printing_messages


def create_agent(name: str) -> ReActAgent:
    """Create an agent with the given name."""
    return ReActAgent(
        name=name,
        sys_prompt=f"You are a student named {name}.",
        model=DashScopeChatModel(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="qwen-max",
            stream=False,  # close streaming for simplicity
        ),
        formatter=DashScopeMultiAgentFormatter(),
    )


async def workflow(
    alice: ReActAgent,
    bob: ReActAgent,
    charlie: ReActAgent,
) -> None:
    """The example workflow for multiple agents."""
    async with MsgHub(
        participants=[alice, bob, charlie],
        announcement=Msg(
            "user",
            "Alice, Bob and Charlie, welcome to the meeting! Let's "
            "meet each other first.",
            "user",
        ),
    ):
        # agent speaks in turn
        await alice()
        await bob()
        await charlie()


async def main() -> None:
    """The main entry for the example."""
    # Create agents
    alice, bob, charlie = [
        create_agent(_) for _ in ["Alice", "Bob", "Charlie"]
    ]

    async for msg, last in stream_printing_messages(
        agents=[alice, bob, charlie],
        coroutine_task=workflow(alice, bob, charlie),
    ):
        print(msg, last)


asyncio.run(main())
