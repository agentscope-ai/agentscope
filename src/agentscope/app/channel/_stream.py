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

    The subscription is live by the time this returns. Replay starts after
    the persisted checkpoint so retained events from an earlier run are not
    delivered as the current reply.

    Args:
        bus (`MessageBus`): The application message bus.
        session_id (`str`): The run's session, whose events are read.

    Returns:
        `AsyncGenerator[dict, None]`: Yields each session event up to and
        including the terminal one. Close it to drop the subscription.
    """
    event_key = MessageBusKeys.session_events(session_id)
    ready = asyncio.Event()
    queue: asyncio.Queue[dict | Exception] = asyncio.Queue()

    async def feeder() -> None:
        """Buffer live subscription events into the local queue."""
        try:
            async for evt in bus.subscribe(event_key, on_ready=ready.set):
                await queue.put(evt)
        except Exception as error:
            await queue.put(error)
            raise
        subscription_error = RuntimeError(
            "Reply stream subscription ended unexpectedly",
        )
        await queue.put(subscription_error)
        raise subscription_error

    feeder_task = asyncio.create_task(feeder())
    ready_task = asyncio.create_task(ready.wait())
    try:
        done, _ = await asyncio.wait(
            {ready_task, feeder_task},
            timeout=_SUBSCRIBE_TIMEOUT_SECS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if feeder_task in done:
            await feeder_task
        if ready_task not in done:
            raise TimeoutError("Timed out waiting for the bus subscription")
        cursor = await bus.registry_get(
            MessageBusKeys.session_event_checkpoint(session_id),
            MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
        )
    except BaseException:
        feeder_task.cancel()
        try:
            await feeder_task
        except (
            asyncio.CancelledError,
            Exception,
        ):  # pylint: disable=broad-except
            pass
        raise
    finally:
        ready_task.cancel()
        try:
            await ready_task
        except asyncio.CancelledError:
            pass
    return _read(bus, event_key, queue, feeder_task, cursor)


async def _read(
    bus: MessageBus,
    event_key: str,
    queue: "asyncio.Queue[dict | Exception]",
    feeder_task: "asyncio.Task",
    after: str | None,
) -> AsyncGenerator[dict, None]:
    """Replay the log, then go live, stopping at the terminal event.

    Deduplicates by ``entry_id`` so the seam between the two is neither
    missed nor double-counted.

    Args:
        bus (`MessageBus`): The application message bus.
        event_key (`str`): The session's event log / channel key.
        queue (`asyncio.Queue[dict | Exception]`): Live events or a
            subscription failure buffered so far.
        feeder_task (`asyncio.Task`): The subscription, cancelled on close.
        after (`str | None`): Persisted checkpoint cursor to replay after.

    Yields:
        `dict`: Each session event, up to and including the terminal one.
    """
    seen: set[str] = set()
    try:
        for entry_id, evt in await bus.log_read(
            event_key,
            since=after,
            max_count=MessageBusKeys.SESSION_REPLAY_MAX_LEN,
        ):
            seen.add(str(entry_id))
            yield evt
            if evt.get("type", "") in _TERMINAL_EVENTS:
                return
        while True:
            evt = await queue.get()
            if isinstance(evt, Exception):
                raise evt
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
