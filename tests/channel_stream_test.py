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

import fakeredis.aioredis
from redis import exceptions as redis_exceptions

from agentscope.app._bus_ops import publish_session_event
from agentscope.app.channel._stream import open_reply_stream
from agentscope.app.message_bus import InMemoryMessageBus, RedisMessageBus
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


class _ResettingPubSub:
    """Raise one connection error from an otherwise real fakeredis pubsub."""

    def __init__(
        self,
        inner: object,
        connection_reset: asyncio.Event,
    ) -> None:
        self._inner = inner
        self._connection_reset = connection_reset
        self._raised = False

    async def get_message(self, *args: object, **kwargs: object) -> object:
        """Simulate the socket error raised when Redis resets a connection."""
        if not self._raised:
            self._raised = True
            self._connection_reset.set()
            raise redis_exceptions.ConnectionError(
                "simulated Redis connection reset",
            )
        return await self._inner.get_message(*args, **kwargs)  # type: ignore

    def __getattr__(self, name: str) -> object:
        """Delegate subscribe cleanup and other pubsub methods."""
        return getattr(self._inner, name)


class _ResettingRedisClient:
    """Return one failing pubsub, then normal pubsubs."""

    def __init__(
        self,
        inner: fakeredis.aioredis.FakeRedis,
        connection_reset: asyncio.Event,
    ) -> None:
        self._inner = inner
        self._connection_reset = connection_reset
        self.pubsub_calls = 0

    def pubsub(self, *args: object, **kwargs: object) -> object:
        """Inject the reset into the first subscription only."""
        self.pubsub_calls += 1
        pubsub = self._inner.pubsub(*args, **kwargs)
        if self.pubsub_calls == 1:
            return _ResettingPubSub(pubsub, self._connection_reset)
        return pubsub

    def __getattr__(self, name: str) -> object:
        """Delegate stream and publish operations to fakeredis."""
        return getattr(self._inner, name)


class _ReplayBarrierRedisBus(RedisMessageBus):
    """Hold the initial replay open while the pubsub connection fails."""

    def __init__(self, client: _ResettingRedisClient) -> None:
        super().__init__()
        self._client = client
        self.replay_started = asyncio.Event()
        self.replay_release = asyncio.Event()
        self.replay_completed = asyncio.Event()

    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Make the replay/live failure window deterministic."""
        if not self.replay_started.is_set():
            self.replay_started.set()
            await self.replay_release.wait()
        result = await super().log_read(key, since, max_count)
        self.replay_completed.set()
        return result


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

    async def test_recovers_after_redis_pubsub_connection_reset(self) -> None:
        """A reset subscription is recovered from the replay log."""
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        connection_reset = asyncio.Event()
        client = _ResettingRedisClient(fake_redis, connection_reset)
        bus = _ReplayBarrierRedisBus(client)
        session_id = "s-redis-reset"
        stream = await open_reply_stream(bus, session_id)
        read_task = asyncio.create_task(anext(stream))

        try:
            await asyncio.wait_for(bus.replay_started.wait(), timeout=2.0)
            await asyncio.wait_for(connection_reset.wait(), timeout=2.0)
            bus.replay_release.set()
            await asyncio.wait_for(bus.replay_completed.wait(), timeout=2.0)
            await _publish(bus, session_id, _end())

            event = await asyncio.wait_for(read_task, timeout=2.0)
            self.assertEqual(event["type"], "REPLY_END")
            self.assertGreaterEqual(client.pubsub_calls, 2)
        finally:
            if not read_task.done():
                read_task.cancel()
            await asyncio.gather(read_task, return_exceptions=True)
            await stream.aclose()
            await fake_redis.aclose()
