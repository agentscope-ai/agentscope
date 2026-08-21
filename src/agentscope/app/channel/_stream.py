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
_TERMINAL_EVENTS = frozenset(
    {
        EventType.REPLY_END,
        EventType.REQUIRE_USER_CONFIRM,
        EventType.REQUIRE_EXTERNAL_EXECUTION,
    },
)


async def event_stream(
    bus: MessageBus,
    session_id: str,
) -> AsyncGenerator[dict, None]:
    """Yield a run's events gap-free, stopping after the terminal one.

    Subscribe **first** (buffering live events), replay the log, then go
    live — deduplicating by ``entry_id`` so the seam is neither missed
    nor double-counted.

    Args:
        bus (`MessageBus`): The application message bus.
        session_id (`str`): The run's session, whose events are read.

    Yields:
        `dict`: Each session event, up to and including the terminal one.
    """
    event_key = MessageBusKeys.session_events(session_id)
    ready = asyncio.Event()
    queue: asyncio.Queue[dict] = asyncio.Queue()
    seen: set[str] = set()

    async def feeder() -> None:
        """Buffer live subscription events into the local queue."""
        try:
            async for evt in bus.subscribe(event_key, on_ready=ready.set):
                await queue.put(evt)
        except asyncio.CancelledError:
            pass

    feeder_task = asyncio.create_task(feeder())
    try:
        await asyncio.wait_for(ready.wait(), timeout=5.0)
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
