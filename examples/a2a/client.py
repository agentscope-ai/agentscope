# -*- coding: utf-8 -*-
"""Chat with a remote A2A 1.0 agent in the terminal.

``A2AAgent`` streams AgentScope events, so it goes straight into
``launch_console`` like a local agent would. Start ``server.py`` first, then
run::

    python client.py [--url http://127.0.0.1:9999] [--verbosity default]
"""
import argparse
import asyncio

import httpx
from a2a.client import A2ACardResolver

from agentscope.agent import A2AAgent
from agentscope.console import launch_console


async def main() -> None:
    """Resolve the remote Agent Card and hand the agent to the console."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:9999")
    parser.add_argument(
        "--verbosity",
        choices=["quiet", "default", "debug"],
        default="default",
    )
    args = parser.parse_args()

    # The card is fetched once, over a client this example owns; A2AAgent
    # builds its own transport from it.
    async with httpx.AsyncClient() as httpx_client:
        card = await A2ACardResolver(
            httpx_client=httpx_client,
            base_url=args.url,
        ).get_agent_card()

    print(f"Connected to {card.name!r} at {args.url}.")
    async with A2AAgent(card) as agent:
        await launch_console(agent, verbosity=args.verbosity)


if __name__ == "__main__":
    asyncio.run(main())
