# -*- coding: utf-8 -*-
"""The main entry for the A2A agent example."""
import asyncio

from agent_card import agent_card

from agentscope.agent import UserAgent, A2AAgent


async def main() -> None:
    """The main entry for the example, where we build a simple conversation
    between the A2A agent and the user."""

    user = UserAgent("user")

    agent = A2AAgent(
        agent_card=agent_card,
    )

    msg = None
    while True:
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break
        msg = await agent(msg)


asyncio.run(main())
