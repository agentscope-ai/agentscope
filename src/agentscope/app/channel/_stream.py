# -*- coding: utf-8 -*-
"""Read one run's reply off the bus as a gap-free event stream.

Consumed by whichever node delivers a channel-bound run's reply. It
reads from the shared bus rather than from the run's own generator, so
the reader and the run are decoupled — the reader can start before the
run does, or attach to one already in flight.
"""
import asyncio
from typing import AsyncGenerator

from ...event import EventType
from ..message_bus import MessageBus, MessageBusKeys

# Events that end a reply's stream: the run either finished or parked
# waiting on something from outside. A parked run publishes no
# ``REPLY_END`` until it is resumed, so treating these as terminal is
# what keeps a reader from waiting on a reply that is not coming.

# How long to wait for the bus subscription to come up.
_SUBSCRIBE_TIMEOUT_SECS = 5.0
_TERMINAL_EVENTS = frozenset(
    {
        EventType.REPLY_END,
        EventType.REQUIRE_USER_CONFIRM,
        EventType.REQUIRE_EXTERNAL_EXECUTION,
    },
)


async def open_reply_stream(
    bus: MessageBus,
    session_id: str,
) -> AsyncGenerator[dict, None]:
    """Subscribe to a run's events and return a gap-free reader.

    The subscription is live by the time this returns, which is what the
    caller needs: a run drops its whole event log when it persists, so a
    reader that only subscribes afterwards would find nothing to replay
    and then wait on a feed that has already gone quiet.

    Args:
        bus (`MessageBus`): The application message bus.
        session_id (`str`): The run's session, whose events are read.

    Returns:
        `AsyncGenerator[dict, None]`: Yields each session event up to and
        including the terminal one. Close it to drop the subscription.
    """
    event_key = MessageBusKeys.session_events(session_id)
    ready = asyncio.Event()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    async def feeder() -> None:
        """Buffer live subscription events into the local queue."""
        try:
            async for evt in bus.subscribe(event_key, on_ready=ready.set):
                await queue.put(evt)
        except asyncio.CancelledError:
            pass

    feeder_task = asyncio.create_task(feeder())
    try:
        await asyncio.wait_for(ready.wait(), timeout=_SUBSCRIBE_TIMEOUT_SECS)
    except BaseException:
        feeder_task.cancel()
        raise
    return _read(bus, event_key, queue, feeder_task)


async def _read(
    bus: MessageBus,
    event_key: str,
    queue: "asyncio.Queue[dict]",
    feeder_task: "asyncio.Task",
) -> AsyncGenerator[dict, None]:
    """Replay the log, then go live, stopping at the terminal event.

    Deduplicates by ``entry_id`` so the seam between the two is neither
    missed nor double-counted.

    Args:
        bus (`MessageBus`): The application message bus.
        event_key (`str`): The session's event log / channel key.
        queue (`asyncio.Queue[dict]`): Live events buffered so far.
        feeder_task (`asyncio.Task`): The subscription, cancelled on close.

    Yields:
        `dict`: Each session event, up to and including the terminal one.
    """
    seen: set[str] = set()
    try:
        for entry_id, evt in await bus.log_read(
            event_key,
            max_count=MessageBusKeys.SESSION_REPLAY_MAX_LEN,
        ):
            seen.add(str(entry_id))
            yield evt
            if evt.get("type", "") in _TERMINAL_EVENTS:
                return
        while True:
            evt = await queue.get()
            eid = evt.get("_entry_id")
            if eid is not None:
                if str(eid) in seen:
                    continue
                seen.add(str(eid))
            yield evt
            if evt.get("type", "") in _TERMINAL_EVENTS:
                return
    finally:
        feeder_task.cancel()
        try:
            await feeder_task
        except (
            asyncio.CancelledError,
            Exception,
        ):  # pylint: disable=broad-except
            pass
