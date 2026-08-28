# -*- coding: utf-8 -*-
"""Connect to an A2A 1.0 agent and run an interactive conversation."""
import asyncio
import os

import httpx

from a2a.client import A2ACardResolver

from agentscope.agent import A2AAgent
from agentscope.event import CustomEvent, TextBlockDeltaEvent
from agentscope.message import UserMsg


async def main() -> None:
    """Resolve an Agent Card and run a stateful multi-turn conversation."""
    agent_url = os.environ.get(
        "A2A_AGENT_URL",
        "http://localhost:9999",
    )
    async with httpx.AsyncClient() as httpx_client:
        resolver = A2ACardResolver(
            httpx_client=httpx_client,
            base_url=agent_url,
        )
        agent_card = await resolver.get_agent_card()

    print(f"Connected to {agent_card.name!r} at {agent_url}.")
    print("Type a message, or 'quit' to exit.")
    async with A2AAgent(agent_card) as agent:
        while True:
            user_text = await asyncio.to_thread(input, "\nYou: ")
            if user_text.strip().lower() in {"quit", "exit"}:
                break
            if not user_text.strip():
                continue

            print("Agent: ", end="", flush=True)
            async for event in agent.reply_stream(
                UserMsg(name="user", content=user_text),
            ):
                if isinstance(event, TextBlockDeltaEvent):
                    print(event.delta, end="", flush=True)
                elif (
                    isinstance(event, CustomEvent)
                    and event.name == "a2a_status_update"
                ):
                    print(f"\n[{event.value['task_state']}]", flush=True)
            print()


if __name__ == "__main__":
    asyncio.run(main())
