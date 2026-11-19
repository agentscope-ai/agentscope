# -*- coding: utf-8 -*-
"""Regression tests for the session SSE replay/live seam."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
import json
import os
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import uuid4

from redis.asyncio import ConnectionPool

from agentscope.app._bus_ops import publish_session_event
from agentscope.app._router._session import stream_session_events
from agentscope.app.message_bus import (
    InMemoryMessageBus,
    MessageBus,
    MessageBusKeys,
    RedisMessageBus,
)


class _Storage:
    """Return one session for the route ownership check."""

    async def get_session(self, *_: object) -> object:
        """Return a sentinel existing session."""
        return object()


class _SeamBus(InMemoryMessageBus):
    """Publish once while the route is reading its replay log."""

    def __init__(self) -> None:
        super().__init__()
        self._published = False

    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Put one event into both the replay and live paths."""
        if not self._published:
            self._published = True
            await publish_session_event(self, "s-1", {"sequence": 2})
        return await super().log_read(
            key,
            since=since,
            max_count=max_count,
        )


class _LegacyBus(InMemoryMessageBus):
    """Expose the original readiness-only subscription signature."""

    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Notify readiness and forward incoming broadcasts."""
        async with aclosing(super().subscribe(key, on_ready=on_ready)) as sub:
            async for event in sub:
                yield event


def _decode(frame: str | bytes) -> dict:
    """Decode one SSE data frame."""
    if isinstance(frame, bytes):
        frame = frame.decode()
    return json.loads(frame.removeprefix("data: ").strip())


class SessionEventStreamTest(IsolatedAsyncioTestCase):
    """The session route changes from replay to live without a gap."""

    async def _assert_replay_then_live(self, bus: MessageBus) -> None:
        """Read a snapshot and keep live data across a checkpoint trim."""
        sid = uuid4().hex
        key = MessageBusKeys.session_events(sid)
        try:
            await publish_session_event(bus, sid, {"frame": "replay"})
            response = await stream_session_events(
                session_id=sid,
                agent_id="a",
                user_id="u",
                storage=_Storage(),  # type: ignore[arg-type]
                message_bus=bus,
                after=None,
            )
            async with aclosing(response.body_iterator) as stream:
                frames = [_decode(await asyncio.wait_for(anext(stream), 2))]
                await publish_session_event(bus, sid, {"frame": "delta"})
                end = await publish_session_event(bus, sid, {"frame": "end"})
                await bus.log_trim(key, before_id=end)
                await publish_session_event(bus, sid, {"frame": "next"})
                for _ in range(3):
                    frame = await asyncio.wait_for(anext(stream), 2)
                    frames.append(_decode(frame))
            self.assertEqual(
                frames,
                [{"frame": f} for f in ("replay", "delta", "end", "next")],
            )
        finally:
            await bus.log_trim(key)

    async def test_legacy_subscribe_signature(self) -> None:
        """Readiness-only custom buses can replay and stay subscribed."""
        async with _LegacyBus() as bus:
            await self._assert_replay_then_live(bus)

    async def test_real_redis_protocols(self) -> None:
        """Exercise decoded PING replies on real RESP2 and RESP3 sockets."""
        url = os.getenv("AGENTSCOPE_TEST_REDIS_URL")
        if not url:
            self.skipTest(
                "Set AGENTSCOPE_TEST_REDIS_URL to run against Redis.",
            )
        for protocol in (2, 3):
            with self.subTest(protocol=protocol):
                pool = ConnectionPool.from_url(
                    url,
                    protocol=protocol,
                    decode_responses=True,
                )
                try:
                    async with RedisMessageBus(connection_pool=pool) as bus:
                        await self._assert_replay_then_live(bus)
                finally:
                    await pool.aclose()

    async def test_event_at_replay_live_seam_is_delivered_once(self) -> None:
        """A seam event is deduplicated without hiding the next event."""
        bus = _SeamBus()
        await publish_session_event(bus, "s-1", {"sequence": 1})
        response = await stream_session_events(
            session_id="s-1",
            agent_id="a-1",
            user_id="u-1",
            storage=_Storage(),  # type: ignore[arg-type]
            message_bus=bus,
            after=None,
        )

        async with aclosing(response.body_iterator) as stream:
            first = _decode(await anext(stream))
            second = _decode(await anext(stream))
            await publish_session_event(bus, "s-1", {"sequence": 3})
            third = _decode(
                await asyncio.wait_for(anext(stream), timeout=1),
            )

        self.assertListEqual(
            [first, second, third],
            [{"sequence": 1}, {"sequence": 2}, {"sequence": 3}],
        )

    async def test_checkpoint_during_replay_keeps_live_delta(self) -> None:
        """A trimmed live delta precedes the retained end exactly once."""
        bus = InMemoryMessageBus()
        start = {"type": "REPLY_START", "reply_id": "r"}
        delta = {
            "type": "TEXT_BLOCK_DELTA",
            "reply_id": "r",
            "block_id": "b",
            "delta": "hello",
        }
        end = {"type": "REPLY_END", "reply_id": "r"}
        await publish_session_event(bus, "s", start)
        response = await stream_session_events(
            session_id="s",
            agent_id="a",
            user_id="u",
            storage=_Storage(),  # type: ignore[arg-type]
            message_bus=bus,
            after=None,
        )
        async with aclosing(response.body_iterator) as stream:
            frames = [_decode(await anext(stream))]
            await publish_session_event(bus, "s", delta)
            cursor = await publish_session_event(bus, "s", end)
            await bus.log_trim(
                MessageBusKeys.session_events("s"),
                before_id=cursor,
            )
            sentinel = {"sequence": "next"}
            await publish_session_event(bus, "s", sentinel)
            while frames[-1] != sentinel:
                frame = await asyncio.wait_for(anext(stream), 1)
                frames.append(_decode(frame))
        self.assertEqual(frames, [start, delta, end, sentinel])

    async def test_live_frames_received_during_snapshot_read(self) -> None:
        """Merge a queued frame that was trimmed before the read returned."""
        bus = InMemoryMessageBus()
        reading = asyncio.Event()
        release = asyncio.Event()
        log_read = bus.log_read

        async def delayed_read(
            key: str,
            since: str | None = None,
            max_count: int = 100,
        ) -> list[tuple[str, dict]]:
            reading.set()
            await release.wait()
            return await log_read(key, since=since, max_count=max_count)

        response = await stream_session_events(
            session_id="s",
            agent_id="a",
            user_id="u",
            storage=_Storage(),  # type: ignore[arg-type]
            message_bus=bus,
            after=None,
        )
        async with aclosing(response.body_iterator) as stream:
            with patch.object(bus, "log_read", side_effect=delayed_read):
                first = asyncio.create_task(anext(stream))
                await asyncio.wait_for(reading.wait(), 1)
                await publish_session_event(bus, "s", {"sequence": 1})
                tail = await publish_session_event(bus, "s", {"sequence": 2})
                await bus.log_trim(MessageBusKeys.session_events("s"), tail)
                release.set()
                frames = [_decode(await asyncio.wait_for(first, 1))]
            await publish_session_event(bus, "s", {"sequence": 3})
            for _ in range(2):
                frame = await asyncio.wait_for(anext(stream), 1)
                frames.append(_decode(frame))
        self.assertEqual(frames, [{"sequence": n} for n in (1, 2, 3)])

    async def test_snapshot_ahead_of_subscription_feeder(self) -> None:
        """A delta in the transport buffer must precede the snapshot end."""
        bus = InMemoryMessageBus()
        read = bus.log_read
        injected = False

        async def read_after_trim(
            key: str,
            since: str | None = None,
            max_count: int = 100,
        ) -> list[tuple[str, dict]]:
            nonlocal injected
            if not injected:
                injected = True
                # In-memory publish and trim do not yield to the feeder.
                await publish_session_event(bus, "s", {"frame": "delta"})
                end = await publish_session_event(bus, "s", {"frame": "end"})
                await bus.log_trim(key, end)
            return await read(key, since=since, max_count=max_count)

        response = await stream_session_events(
            session_id="s",
            agent_id="a",
            user_id="u",
            storage=_Storage(),  # type: ignore[arg-type]
            message_bus=bus,
            after=None,
        )
        async with aclosing(response.body_iterator) as stream:
            with patch.object(bus, "log_read", side_effect=read_after_trim):
                frames = [_decode(await asyncio.wait_for(anext(stream), 1))]
                await publish_session_event(bus, "s", {"frame": "sentinel"})
                while frames[-1] != {"frame": "sentinel"}:
                    frame = await asyncio.wait_for(anext(stream), 1)
                    frames.append(_decode(frame))
        self.assertEqual(
            frames,
            [{"frame": name} for name in ("delta", "end", "sentinel")],
        )

    async def test_connected_client_recovers_failed_live_delivery(
        self,
    ) -> None:
        """Recover a missed end on heartbeat or before the next live event."""
        for next_live in (False, True):
            with self.subTest(next_live=next_live), patch(
                "agentscope.app._router._session._HEARTBEAT_INTERVAL_SECS",
                0.02,
            ):
                bus = InMemoryMessageBus()
                response = await stream_session_events(
                    session_id="s",
                    agent_id="a",
                    user_id="u",
                    storage=_Storage(),  # type: ignore[arg-type]
                    message_bus=bus,
                    after=None,
                )
                async with aclosing(response.body_iterator) as stream:
                    # A heartbeat proves replay is over and the client is live.
                    self.assertEqual(await anext(stream), ":\n\n")
                    end = {"type": "REPLY_END", "reply_id": "first"}
                    with patch.object(bus, "publish", side_effect=OSError):
                        await publish_session_event(
                            bus,
                            "s",
                            end,
                            require_live_delivery=False,
                        )
                    expected = [end]
                    if next_live:
                        start = {"type": "REPLY_START", "reply_id": "next"}
                        await publish_session_event(bus, "s", start)
                        expected.append(start)
                    frames = [
                        _decode(await asyncio.wait_for(anext(stream), 1))
                        for _ in expected
                    ]
                    self.assertEqual(frames, expected)
                    self.assertEqual(await anext(stream), ":\n\n")
