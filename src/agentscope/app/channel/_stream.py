# -*- coding: utf-8 -*-
"""Read one run's reply off the bus as a gap-free event stream.

Consumed by whichever node delivers a channel-bound run's reply. It
reads from the shared bus rather than from the run's own generator, so
the reader and the run are decoupled — the reader can start before the
run does, or attach to one already in flight.
"""
import asyncio
from typing import AsyncGenerator

from ..._logging import logger
from ...event import EventType
from ..message_bus import MessageBus, MessageBusKeys

# Events that end a reply's stream: the run either finished or parked
# waiting on something from outside. A parked run publishes no
# ``REPLY_END`` until it is resumed, so treating these as terminal is
# what keeps a reader from waiting on a reply that is not coming.

# How long to wait for the bus subscription to come up.
_SUBSCRIBE_TIMEOUT_SECS = 5.0
# Retry delay bounds for a transient subscription failure. The replay log
# makes retrying safe: events published while the subscription is down are
# recovered after the next subscription is ready.
_RECONNECT_INITIAL_DELAY_SECS = 0.1
_RECONNECT_MAX_DELAY_SECS = 5.0
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
        """Buffer live events and recover a subscription that disconnects."""
        cursor: str | None = None
        first_subscription = True
        reconnect_delay = _RECONNECT_INITIAL_DELAY_SECS

        async def _cancel_subscription(task: asyncio.Task) -> None:
            """Stop one subscription attempt without leaking its task."""
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        async def _replay_from_cursor() -> None:
            """Queue every log entry newer than the live-feed cursor."""
            nonlocal cursor
            while True:
                entries = await bus.log_read(
                    event_key,
                    since=cursor,
                    max_count=MessageBusKeys.SESSION_REPLAY_MAX_LEN,
                )
                for entry_id, evt in entries:
                    await queue.put({**evt, "_entry_id": entry_id})
                    cursor = str(entry_id)
                if len(entries) < MessageBusKeys.SESSION_REPLAY_MAX_LEN:
                    return

        while True:
            subscription_ready = asyncio.Event()

            def _on_ready() -> None:
                """Signal both initial readiness and this retry attempt."""
                ready.set()
                subscription_ready.set()

            async def _consume_subscription() -> None:
                """Forward one subscription attempt into the local queue."""
                nonlocal cursor
                async for evt in bus.subscribe(
                    event_key,
                    on_ready=_on_ready,
                ):
                    await queue.put(evt)
                    entry_id = evt.get("_entry_id")
                    if entry_id is not None:
                        cursor = str(entry_id)

            subscription_task = asyncio.create_task(_consume_subscription())
            ready_wait_task = asyncio.create_task(subscription_ready.wait())
            try:
                done, _ = await asyncio.wait(
                    {ready_wait_task, subscription_task},
                    timeout=_SUBSCRIBE_TIMEOUT_SECS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    raise asyncio.TimeoutError(
                        "message bus subscription did not become ready",
                    )
                if not subscription_ready.is_set():
                    await subscription_task
                    raise RuntimeError("message bus subscription ended early")

                if not first_subscription:
                    await _replay_from_cursor()
                first_subscription = False
                reconnect_delay = _RECONNECT_INITIAL_DELAY_SECS
                await subscription_task
                raise RuntimeError("message bus subscription ended")
            except asyncio.CancelledError:
                await _cancel_subscription(subscription_task)
                raise
            except Exception as exc:  # pylint: disable=broad-except
                await _cancel_subscription(subscription_task)
                logger.warning(
                    "channel reply subscription lost for %s; retrying: %s",
                    event_key,
                    exc,
                )
            finally:
                if not ready_wait_task.done():
                    ready_wait_task.cancel()
                await asyncio.gather(
                    ready_wait_task,
                    return_exceptions=True,
                )

            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(
                reconnect_delay * 2,
                _RECONNECT_MAX_DELAY_SECS,
            )

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
