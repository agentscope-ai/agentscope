# -*- coding: utf-8 -*-
"""The Voice chat room"""
import asyncio
from asyncio import Queue

from ..agent import RealtimeAgent
from ..realtime import ClientEvents, ServerEvents


class ChatRoom:
    """The chat room abstraction to broadcast messages among multiple realtime
    agents, and handle the messages from the frontend.
    """

    def __init__(self, agents: list[RealtimeAgent]) -> None:
        """Initialize the ChatRoom class.

        Args:
            agents (`list[RealtimeAgent]`):
                The list of agents participating in the chat room.
        """
        self.agents = agents

        # The queue used to gather messages from all agents and push them to
        # the frontend.
        self._queue = Queue()

        self._task = None

    async def start(self, outgoing_queue: Queue) -> None:
        """Establish connections for all agents in the chat room.

        Args:
            outgoing_queue (`Queue`):
                The queue to push messages to the frontend, which will be used
                by all agents to push their messages.
        """

        for agent in self.agents:
            await agent.start(self._queue)

        # Start the forwarding loop.
        self._task = asyncio.create_task(self._forward_loop(outgoing_queue))

    async def _forward_loop(self, outgoing_queue: Queue) -> None:
        """The loop to forward messages from all agents to the frontend and
        the other agents.

        Args:
            outgoing_queue (`Queue`):
                The queue to push messages to the frontend.
        """

        while True:
            # Obtain the message from the client frontend
            event = await self._queue.get()

            # Only push ServerEvents to the frontend, not ClientEvents
            # to avoid echoing client messages back
            if isinstance(event, ClientEvents.EventBase):
                # Push the message to the frontend queue.
                for agent in self.agents:
                    await agent.handle_input(event)

            elif isinstance(event, ServerEvents.EventBase):
                # Broadcast the message to all agents except the sender.
                # Use create_task instead of gather to avoid blocking

                # Forward the agent/server events to the frontend
                await outgoing_queue.put(event)

                # Broadcast to other agents
                sender_id = getattr(event, "agent_id", None)
                if sender_id:
                    for agent in self.agents:
                        if agent.id != sender_id:
                            await agent.handle_input(event)

    async def stop(self) -> None:
        """Close connections for all agents in the chat room."""

        for agent in self.agents:
            await agent.stop()

        # Close the forwarding loop.
        if not self._task.done():
            self._task.cancel()

    async def handle_input(self, event: ClientEvents.EventBase) -> None:
        """Handle input message from the frontend and distribute it to all
        agents in the chat room.

        Args:
            event (`ClientEvents.EventBase`):
                The event from the frontend.
        """
        await self._queue.put(event)
