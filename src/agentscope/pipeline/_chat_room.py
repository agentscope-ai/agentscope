# -*- coding: utf-8 -*-
"""The Voice chat room"""
import asyncio
from asyncio import Queue

from ..agent import RealtimeAgentBase
from ..realtime import ClientEvents


class ChatRoom:
    """The chat room class to manage multiple agents in a chat room."""

    def __init__(self, agents: list[RealtimeAgentBase]) -> None:
        """Initialize the ChatRoom class.

        Args:
            agents (`list[RealtimeAgentBase]`):
                The list of agents participating in the chat room.
        """
        self.agents = agents

        # The queue used to gather messages from all agents and push them to
        # the frontend.
        self._queue = Queue()

        self._task = None

    async def start(self, queue: Queue) -> None:
        """Establish connections for all agents in the chat room.

        Args:
            queue (`Queue`):
                The queue to push messages to the frontend, which will be used
                by all agents to push their messages.
        """

        for agent in self.agents:
            await agent.start(self._queue)

        # Start the forwarding loop.
        self._task = asyncio.create_task(self._forward_loop(queue))

    async def _forward_loop(self, queue: Queue) -> None:
        """The loop to forward messages from all agents to the frontend and
        the other agents."""

        while True:
            msg = await self._queue.get()

            # Push the message to the frontend queue.
            await queue.put(msg)

            # TODO: maybe we should filter the events here, e.g. agent session
            #  created, updated or the other non-message events.

            # Broadcast the message to all agents except the sender.
            # Use create_task instead of gather to avoid blocking
            sender_id = getattr(msg, "agent_id", None)
            if sender_id:
                for agent in self.agents:
                    if agent.id != sender_id:
                        asyncio.create_task(agent.handle_input(msg))

    async def stop(self) -> None:
        """Close connections for all agents in the chat room."""

        for agent in self.agents:
            await agent.stop()

        # Close the forwarding loop.
        if not self._task.done():
            self._task.cancel()

    async def handle_input(self, event: ClientEvents) -> None:
        """Handle input message from the frontend and distribute it to all
        agents in the chat room."""
        await self._queue.put(event)
