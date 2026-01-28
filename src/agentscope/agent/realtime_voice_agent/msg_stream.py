# -*- coding: utf-8 -*-
"""Event-based Message Stream with central queue and dispatch loop.

This module provides:
- Central queue for collecting AgentEvents from all agents
- Dispatch loop for distributing events to agent incoming queues
- Manages agent lifecycle (start/stop)
"""

import asyncio
from typing import Callable, Sequence

from ..._logging import logger

from .events import AgentEvent
from .agent import RealtimeVoiceAgent


class EventMsgStream:
    """Event-based message stream for multi-agent voice communication.

    This class manages:
    1. A central queue where all agents push their AgentEvents
    2. A dispatch loop that distributes events to other agents
    3. Agent lifecycle (start/stop)

    Architecture:
        Agent A -> callback -> AgentEvent -> queue
        Agent B -> callback -> AgentEvent -> queue
                                              |
                                      dispatch_loop
                                              |
                          +-------------------+-------------------+
                          |                   |                   |
                          v                   v                   v
                  Agent A.incoming      Agent B.incoming    External callback
                  (filtered)            (filtered)          (all events)

    Example:
        .. code-block:: python

            agent1 = RealtimeVoiceAgent(name="agent1", model=model1)
            agent2 = RealtimeVoiceAgent(name="agent2", model=model2)

            stream = EventMsgStream(agents=[agent1, agent2])

            # Optional: register external callback for WebSocket forwarding
            async def forward_to_websocket(event: AgentEvent):
                await websocket.send_json(event_to_dict(event))

            stream.on_event = forward_to_websocket

            await stream.start()
            await stream.join()  # Wait until stopped
    """

    def __init__(
        self,
        agents: Sequence[RealtimeVoiceAgent],
        queue_max_size: int = 1000,
    ) -> None:
        """Initialize the event message stream.

        Args:
            agents (`Sequence[RealtimeVoiceAgent]`):
                List of RealtimeVoiceAgent instances to manage.
            queue_max_size (`int`, optional):
                Maximum size of the central queue. Defaults to 1000.
        """
        self._agents: list[RealtimeVoiceAgent] = list(agents)
        self._queue_max_size = queue_max_size

        # Central queue for AgentEvents
        self._queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue(
            maxsize=queue_max_size,
        )

        # State
        self._initialized = False
        self._stop_event = asyncio.Event()
        self._dispatch_task: asyncio.Task | None = None

        # External event callback (e.g., for WebSocket forwarding)
        self.on_event: Callable[[AgentEvent], None] | None = None

    async def start(self) -> None:
        """Start the message stream and all agents.

        This method:
        1. Starts all agents (connects to models)
        2. Starts the dispatch loop
        """
        if self._initialized:
            raise RuntimeError("EventMsgStream already started")

        self._stop_event.clear()

        # Start all agents with our central queue
        start_tasks = [agent.start(self._queue) for agent in self._agents]
        await asyncio.gather(*start_tasks)

        # Start dispatch loop
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())

        self._initialized = True
        logger.info(
            "EventMsgStream started with %d agents",
            len(self._agents),
        )

    async def _dispatch_loop(self) -> None:
        """Dispatch AgentEvents from central queue to agent incoming queues.

        This loop:
        1. Gets events from central queue
        2. For each event, dispatches to all agents except the sender
        3. Also invokes external callback if registered
        """
        logger.info("Dispatch loop started")

        try:
            while not self._stop_event.is_set():
                try:
                    # Wait for event with timeout
                    event = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=0.1,
                    )

                    # None signals shutdown
                    if event is None:
                        break

                    # Dispatch to all agents
                    for agent in self._agents:
                        try:
                            agent.incoming_queue.put_nowait(event)
                        except asyncio.QueueFull:
                            logger.warning(
                                "Agent %s incoming queue full",
                                agent.name,
                            )

                    # Invoke external callback
                    if self.on_event is not None:
                        try:
                            self.on_event(
                                event,
                            )  # pylint: disable=not-callable
                        except Exception as e:
                            logger.error("External callback error: %s", e)

                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

        except Exception as e:
            logger.error("Dispatch loop error: %s", e)
        finally:
            logger.info("Dispatch loop ended")

    async def stop(self) -> None:
        """Stop the message stream and all agents.

        This method:
        1. Sets stop event
        2. Puts None to queue to signal dispatch loop
        3. Cancels dispatch task
        4. Closes all agents
        """
        if not self._initialized:
            return

        self._stop_event.set()

        # Signal dispatch loop to stop
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

        # Cancel dispatch task
        if self._dispatch_task and not self._dispatch_task.done():
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        # Stop and close all agents
        for agent in self._agents:
            agent.stop()

        close_tasks = [agent.close() for agent in self._agents]
        await asyncio.gather(*close_tasks, return_exceptions=True)

        self._initialized = False
        logger.info("EventMsgStream stopped")

    async def join(self, timeout: float | None = None) -> None:
        """Wait until the stream is stopped.

        Args:
            timeout (`float`, optional):
                Maximum time to wait in seconds. None for indefinite.

        Raises:
            asyncio.TimeoutError:
                If timeout expires.
        """
        if not self._initialized:
            return

        await asyncio.wait_for(self._stop_event.wait(), timeout=timeout)

    def push_event(self, event: AgentEvent) -> None:
        """Push an external AgentEvent to the queue.

        This can be used to inject events from external sources
        (e.g., user audio from WebSocket).

        Args:
            event (`AgentEvent`):
                The AgentEvent to push.
        """
        if not self._initialized:
            logger.warning("MsgStream not started, ignoring event")
            return

        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("Central queue full, dropping event")

    async def run_forever(self) -> None:
        """Start and run until stopped.

        Convenience method for simple scripts.
        """
        await self.start()
        await self.join()

    async def __aenter__(self) -> "EventMsgStream":
        """Enter async context and start."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Exit async context and stop."""
        await self.stop()

    @property
    def agents(self) -> list[RealtimeVoiceAgent]:
        """Get list of managed agents.

        Returns:
            `list[RealtimeVoiceAgent]`:
                The list of managed agents.
        """
        return self._agents

    @property
    def is_running(self) -> bool:
        """Check if the stream is running.

        Returns:
            `bool`:
                True if running, False otherwise.
        """
        return self._initialized and not self._stop_event.is_set()

    @property
    def queue_size(self) -> int:
        """Get current queue size.

        Returns:
            `int`:
                The current queue size.
        """
        return self._queue.qsize()
