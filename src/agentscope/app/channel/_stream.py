# -*- coding: utf-8 -*-
"""Read one reply segment off the bus as a gap-free event stream.

Consumed by whichever node delivers a channel-bound run's reply. It
reads from the shared bus rather than from the run's own generator, so
the reader and the run are decoupled — the reader can start before the
run does, or attach to one already in flight.
"""
import asyncio
from contextlib import aclosing
from typing import AsyncGenerator

from ...event import EventType
from ..message_bus import MessageBus, MessageBusKeys

# Events that end a reply's stream: the reply either finished or parked
# waiting on something from outside. A parked reply publishes no
# ``REPLY_END`` until it is resumed, so treating these as terminal is
# what keeps a reader from waiting on a reply that is not coming.

# How long to wait for the bus subscription to come up.
_SUBSCRIBE_TIMEOUT_SECS = 5.0
_closing_feeders: set[asyncio.Task[None]] = set()
_TERMINAL_EVENTS = frozenset(
    {
        EventType.REPLY_END,
        EventType.REQUIRE_USER_CONFIRM,
        EventType.REQUIRE_EXTERNAL_EXECUTION,
    },
)


def _feeder_closed(task: asyncio.Task[None]) -> None:
    """Release a failed setup's feeder and observe any cleanup error."""
    _closing_feeders.discard(task)
    if not task.cancelled():
        task.exception()


class _ReplyStream:
    """A reply reader with explicit ownership of its live subscription."""

    def __init__(
        self,
        replay: list[tuple[str, dict]],
        queue: asyncio.Queue,
        feeder: asyncio.Task,
    ) -> None:
        """Bind a replay snapshot and an already active subscription.

        Args:
            replay (`list[tuple[str, dict]]`): Log entries for this segment.
            queue (`asyncio.Queue`): Buffered live events or read failures.
            feeder (`asyncio.Task`): Task that owns the live subscription.
        """
        self.subscription = feeder
        self._queue = queue
        self._reader = self._read(replay, queue)
        feeder.add_done_callback(_observe_feeder)

    def __aiter__(self) -> "_ReplyStream":
        """Return this reply's asynchronous iterator.

        Returns:
            `_ReplyStream`: This reader.
        """
        return self

    async def __anext__(self) -> dict:
        """Read the next event from this reply segment.

        Returns:
            `dict`: The next event, including the segment's terminal event.
        """
        return await anext(self._reader)

    def cancel(self) -> None:
        """Release the subscription even if iteration never started."""
        if not self.subscription.cancelling():
            self.subscription.cancel()

    def abort(self) -> None:
        """End an abandoned producer without waiting for a later run."""
        self.cancel()
        self._queue.put_nowait(RuntimeError("Reply producer stopped"))

    async def aclose(self) -> None:
        """Close both the reader and its independently started feeder."""
        self.cancel()
        await self._reader.aclose()
        await asyncio.gather(self.subscription, return_exceptions=True)

    async def _read(
        self,
        replay: list[tuple[str, dict]],
        queue: asyncio.Queue,
    ) -> AsyncGenerator[dict, None]:
        """Replay buffered events, then read live events without duplicates.

        Args:
            replay (`list[tuple[str, dict]]`): Snapshot captured at setup.
            queue (`asyncio.Queue`): Buffered live events or read failures.

        Yields:
            `dict`: Each event up to and including the first terminal event.
        """
        seen: set[str] = set()
        try:
            for entry_id, event in replay:
                seen.add(str(entry_id))
                yield event
                if event.get("type") in _TERMINAL_EVENTS:
                    return
            while True:
                event = await queue.get()
                if isinstance(event, Exception):
                    raise event
                entry_id = event.get("_entry_id")
                if entry_id is not None:
                    if str(entry_id) in seen:
                        continue
                    seen.add(str(entry_id))
                yield event
                if event.get("type") in _TERMINAL_EVENTS:
                    return
        finally:
            self.cancel()
            await asyncio.gather(self.subscription, return_exceptions=True)


def _observe_feeder(task: asyncio.Task) -> None:
    """Observe errors even when a platform never reads its stream.

    Args:
        task (`asyncio.Task`): The completed subscription task.
    """
    if not task.cancelled():
        task.exception()


async def open_reply_stream(
    bus: MessageBus,
    session_id: str,
    *,
    after: str | None = None,
) -> _ReplyStream:
    """Subscribe and snapshot replay before the producer can proceed.

    ``after`` is an exclusive turn-local event cursor. By default use the
    persisted session checkpoint. The live feeder buffers independently of
    platform reads, and stops at the first reply terminal event.

    Args:
        bus (`MessageBus`): The application message bus.
        session_id (`str`): The session whose reply events are read.
        after (`str | None`, optional): Exclusive replay cursor. If omitted,
            use the persisted session checkpoint.

    Returns:
        `_ReplyStream`: A reader that owns the live subscription. Close it
        when the delivery finishes, even if it was never iterated.
    """
    event_key = MessageBusKeys.session_events(session_id)
    ready = asyncio.Event()
    queue: asyncio.Queue[dict | Exception] = asyncio.Queue()

    async def feeder() -> None:
        """Buffer live events and stop at this reply segment's terminal."""
        try:
            async with aclosing(
                bus.subscribe(event_key, on_ready=ready.set),
            ) as subscription:
                async for event in subscription:
                    await queue.put(event)
                    if event.get("type") in _TERMINAL_EVENTS:
                        return
        except Exception as error:
            await queue.put(error)
            raise
        ended = RuntimeError("Reply stream subscription ended unexpectedly")
        await queue.put(ended)
        raise ended

    feeder_task = asyncio.create_task(feeder())
    ready_task = asyncio.create_task(ready.wait())
    try:
        done, _ = await asyncio.wait(
            {ready_task, feeder_task},
            timeout=_SUBSCRIBE_TIMEOUT_SECS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not ready.is_set():
            if feeder_task in done:
                await feeder_task
            raise TimeoutError("Timed out waiting for the bus subscription")
        if after is None:
            after = await bus.registry_get(
                MessageBusKeys.session_event_checkpoint(session_id),
                MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
            )
        replay = await bus.log_read(
            event_key,
            since=after,
            max_count=MessageBusKeys.SESSION_REPLAY_MAX_LEN,
        )
        ready_task.cancel()
        await asyncio.gather(ready_task, return_exceptions=True)
    except BaseException:
        # Setup failure must not wait on a bus with blocked cleanup.
        _closing_feeders.add(feeder_task)
        feeder_task.add_done_callback(_feeder_closed)
        feeder_task.cancel()
        raise
    finally:
        ready_task.cancel()
    return _ReplyStream(replay, queue, feeder_task)
