# -*- coding: utf-8 -*-
"""Tests for the channel reply-delivery stream.

``event_stream`` replaced a durable outbound queue: instead of handing
a run off to whichever node held the channel's connection, the node
running the agent reads the run's events off the bus and delivers them
itself. Missing an event or replaying one twice is a lost or duplicated
reply, and never terminating strands the session, so those are what
these cover.
"""
import asyncio
from contextlib import aclosing
from unittest import IsolatedAsyncioTestCase

from agentscope.app._bus_ops import publish_session_event
from agentscope.app.channel._stream import open_reply_stream
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.event import (
    ReplyEndEvent,
    ReplyStartEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
)
from agentscope.types import ReplyFinishedReason


async def _drain(bus: InMemoryMessageBus, session_id: str) -> list[str]:
    """Collect the stream's event types until it terminates."""
    types: list[str] = []
    stream = await open_reply_stream(bus, session_id)
    async with aclosing(stream) as events:
        async for evt in events:
            types.append(evt.get("type", ""))
    return types


async def _publish(
    bus: InMemoryMessageBus,
    session_id: str,
    event: object,
) -> None:
    """Publish one agent event onto the session's stream."""
    await publish_session_event(
        bus,
        session_id,
        event.model_dump(mode="json"),
    )


def _start() -> ReplyStartEvent:
    """A reply-start event."""
    return ReplyStartEvent(reply_id="r-1", session_id="s-1", name="a")


def _end() -> ReplyEndEvent:
    """A terminal reply-end event."""
    return ReplyEndEvent(
        reply_id="r-1",
        session_id="s-1",
        name="a",
        finished_reason=ReplyFinishedReason.COMPLETED,
    )


def _confirm() -> RequireUserConfirmEvent:
    """A run parked awaiting the user's approval."""
    return RequireUserConfirmEvent(
        reply_id="r-1",
        session_id="s-1",
        name="a",
        tool_calls=[],
    )


def _external() -> RequireExternalExecutionEvent:
    """A run parked awaiting an external executor."""
    return RequireExternalExecutionEvent(
        reply_id="r-1",
        session_id="s-1",
        name="a",
        tool_calls=[],
    )


class _SeamBus(InMemoryMessageBus):
    """Publishes an event while the replay read is in flight.

    That reproduces the one case the stream deduplicates: an event
    arriving after the subscription opened but before the replay
    finished is written to the log *and* pushed to the live feed.
    """

    def __init__(self) -> None:
        super().__init__()
        self._seam_published = False

    async def log_read(self, key: str, **kwargs: object) -> list:
        """Slip one event into the window, then replay as usual."""
        if not self._seam_published:
            self._seam_published = True
            await _publish(self, "s-1", _start())
        return await super().log_read(key, **kwargs)


class EventStreamTest(IsolatedAsyncioTestCase):
    """The stream is gap-free and always terminates."""

    async def test_replays_events_published_before_subscribing(
        self,
    ) -> None:
        """A run that finished before delivery started is still sent —
        this is what makes a late reader safe."""
        bus = InMemoryMessageBus()
        await _publish(bus, "s-1", _start())
        await _publish(bus, "s-1", _end())

        self.assertListEqual(
            await _drain(bus, "s-1"),
            ["REPLY_START", "REPLY_END"],
        )

    async def test_delivers_events_published_while_streaming(self) -> None:
        """The common case: delivery starts first, events arrive after."""
        bus = InMemoryMessageBus()

        async def _run() -> None:
            await asyncio.sleep(0.01)
            await _publish(bus, "s-1", _start())
            await _publish(bus, "s-1", _end())

        drained, _ = await asyncio.gather(_drain(bus, "s-1"), _run())
        self.assertListEqual(drained, ["REPLY_START", "REPLY_END"])

    async def test_seam_events_are_not_delivered_twice(self) -> None:
        """An event landing between subscribe and replay reaches the
        stream both ways; it must be yielded once.

        This is why the stream tracks entry ids, and the window is too
        narrow to hit by timing, so the bus forces it.
        """
        bus = _SeamBus()
        await _publish(bus, "s-1", _start())

        async def _finish() -> None:
            await asyncio.sleep(0.02)
            await _publish(bus, "s-1", _end())

        drained, _ = await asyncio.gather(_drain(bus, "s-1"), _finish())
        self.assertListEqual(
            drained,
            ["REPLY_START", "REPLY_START", "REPLY_END"],
        )

    async def test_stops_at_the_terminal_event(self) -> None:
        """Anything published after the run ended belongs to the next
        reply, not this delivery."""
        bus = InMemoryMessageBus()
        await _publish(bus, "s-1", _start())
        await _publish(bus, "s-1", _end())
        await _publish(bus, "s-1", _start())

        self.assertListEqual(
            await _drain(bus, "s-1"),
            ["REPLY_START", "REPLY_END"],
        )

    async def test_a_run_parked_on_confirmation_terminates(self) -> None:
        """A parked run publishes no ``REPLY_END`` until it is resumed."""
        bus = InMemoryMessageBus()
        await _publish(bus, "s-1", _start())
        await _publish(bus, "s-1", _confirm())

        self.assertListEqual(
            await asyncio.wait_for(_drain(bus, "s-1"), timeout=2.0),
            ["REPLY_START", "REQUIRE_USER_CONFIRM"],
        )

    async def test_a_run_parked_on_external_execution_terminates(
        self,
    ) -> None:
        """The same, for a tool executed outside the agent. Waiting for a
        ``REPLY_END`` here would block delivery while the caller holds
        the session lock, so nothing could resume the run."""
        bus = InMemoryMessageBus()
        await _publish(bus, "s-1", _start())
        await _publish(bus, "s-1", _external())

        self.assertListEqual(
            await asyncio.wait_for(_drain(bus, "s-1"), timeout=2.0),
            ["REPLY_START", "REQUIRE_EXTERNAL_EXECUTION"],
        )
