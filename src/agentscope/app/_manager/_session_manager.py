# -*- coding: utf-8 -*-
"""The session manager for

- session replay: replaying persisted logs and in-flight buffered outputs
  to a client that joins a session mid-execution
- session monitor: tracking which sessions are currently running and
  serialising concurrent requests for the same session
"""
import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from ...event import AgentEvent

_SENTINEL = object()
"""Sentinel pushed to subscriber queues to signal end-of-stream."""


@dataclass
class _SessionRun:
    """State for a single in-flight agent run."""

    buffer: list[AgentEvent] = field(default_factory=list)
    """All events produced so far; used to replay to late-joining clients."""

    subscribers: list[asyncio.Queue] = field(default_factory=list)
    """One queue per active SSE subscriber; each receives every new event."""

    async def publish(self, event: AgentEvent) -> None:
        """Append *event* to the replay buffer and fan it out to all subscribers.

        Args:
            event (`AgentEvent`): The event to publish.
        """
        self.buffer.append(event)
        for q in self.subscribers:
            await q.put(event)

    async def close_subscribers(self) -> None:
        """Push the sentinel to every subscriber queue to signal end-of-stream."""
        for q in self.subscribers:
            await q.put(_SENTINEL)


class SessionManager:
    """Manages in-flight agent runs per session.

    Responsibilities:

    - **Serialisation**: at most one active run per ``session_id`` at a time;
      additional callers queue on an ``asyncio.Lock`` and run in order.
    - **Fan-out buffer**: every event produced during a run is appended to a
      replay buffer *and* pushed to all active SSE subscriber queues so that
      clients joining mid-run receive the full event history.
    - **Lifecycle**: the buffer and subscriber list are created when a run
      starts and discarded when it ends, keeping memory bounded.
    """

    def __init__(self) -> None:
        """Initialise the session manager."""
        self._locks: dict[str, asyncio.Lock] = {}
        self._runs: dict[str, _SessionRun] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        """Return (creating if necessary) the per-session serialisation lock."""
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    # ------------------------------------------------------------------
    # Producer API  (used by the chat endpoint)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def run(self, session_id: str) -> AsyncGenerator[_SessionRun, None]:
        """Async context manager that serialises runs for *session_id*.

        Acquires the per-session lock (queuing if another run is active),
        creates a fresh :class:`_SessionRun`, and tears it down on exit.

        Usage::

            async with session_manager.run(session_id) as run:
                async for event in agent.reply(msg):
                    await run.publish(event)
                    yield f"data: {event.model_dump_json()}\\n\\n"

        Args:
            session_id (`str`): The session to run against.

        Yields:
            `_SessionRun`: The active run object; call :meth:`_SessionRun.publish`
            to broadcast each event.
        """
        lock = self._get_lock(session_id)
        async with lock:
            session_run = _SessionRun()
            self._runs[session_id] = session_run
            try:
                yield session_run
            finally:
                await session_run.close_subscribers()
                self._runs.pop(session_id, None)

    # ------------------------------------------------------------------
    # Consumer API  (used by the stream endpoint)
    # ------------------------------------------------------------------

    def is_running(self, session_id: str) -> bool:
        """Return ``True`` if *session_id* has an active run.

        Args:
            session_id (`str`): The session to check.

        Returns:
            `bool`: Whether a run is currently active.
        """
        return session_id in self._runs

    async def subscribe(
        self,
        session_id: str,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Async generator that yields all events for an active run.

        Replays events already in the buffer, then streams new events as they
        arrive.  Yields nothing if *session_id* has no active run.

        The replay/subscribe handoff is race-free: the subscriber queue is
        registered *before* the buffer snapshot is taken, so no event can be
        missed between the two steps (asyncio is single-threaded; no ``await``
        separates the two operations).

        Args:
            session_id (`str`): The session to subscribe to.

        Yields:
            `AgentEvent`: Events in arrival order.
        """
        run = self._runs.get(session_id)
        if run is None:
            return

        queue: asyncio.Queue = asyncio.Queue()
        # Register BEFORE snapshotting the buffer length so that events
        # added after this point go to the queue, not the buffer only.
        run.subscribers.append(queue)
        replay_until = len(run.buffer)

        # Replay events that existed before we subscribed.
        for event in run.buffer[:replay_until]:
            yield event

        # Stream events produced after we subscribed.
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            yield item

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """Cancel all active runs on application shutdown.

        Clears internal state; in-flight SSE generators will receive
        ``StopAsyncIteration`` when their queues are garbage-collected.
        """
        self._runs.clear()
        self._locks.clear()
