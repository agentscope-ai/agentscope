# -*- coding: utf-8 -*-
"""Parallel Multi-Perspective Discussion System."""
import asyncio
from datetime import datetime
from typing import Any

import numpy as np

from agentscope.agent import AgentBase
from agentscope.message import Msg
from agentscope.pipeline import fanout_pipeline


class ExampleAgent(AgentBase):
    """The example agent used to label the time."""

    def __init__(self, name: str) -> None:
        """The constructor of the example agent

        Args:
            name (`str`):
                The agent name.
        """
        super().__init__()
        self.name = name

    async def reply(self, *args: Any, **kwargs: Any) -> Msg:
        """The reply function of the example agent."""
        # we record the start time
        start_time = datetime.now()
        await self.print(
            Msg(
                self.name,
                f"begins at {start_time.strftime('%H:%M:%S.%f')}",
                "assistant",
            ),
        )

        # Sleep some time
        await asyncio.sleep(np.random.choice([2, 3, 4]))

        end_time = datetime.now()
        msg = Msg(
            self.name,
            f"finishes at {end_time.strftime('%H:%M:%S.%f')}",
            "user",
            # Add some metadata for demonstration
            metadata={
                "time": (end_time - start_time).total_seconds(),
            },
        )
        await self.print(msg)
        return msg

    async def handle_interrupt(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Msg:
        """We leave this function unimplemented in this example, because we
        won't use the interrupt functionality"""

    async def observe(self, *args: Any, **kwargs: Any) -> None:
        """Similar with the handle_interrupt function, leaving this empty"""


async def main() -> None:
    """The main entry of the concurrent example."""
    alice = ExampleAgent("Alice")
    bob = ExampleAgent("Bob")
    chalice = ExampleAgent("Chalice")

    print("Use 'asyncio.gather' to run the agents concurrently:")
    futures = [alice(), bob(), chalice()]

    await asyncio.gather(*futures)

    print("\n\nUse fanout pipeline to run the agents concurrently:")
    collected_res = await fanout_pipeline(
        agents=[alice, bob, chalice],
        enable_gather=True,
    )
    # Print the collected results
    print("\n\nThe collected time used by each agent:")
    for res in collected_res:
        print(f"{res.name}: {res.metadata['time']} seconds")

    print("\nThe average time used:")
    avg_time = np.mean([res.metadata["time"] for res in collected_res])
    print(f"{avg_time} seconds")


asyncio.run(main())
