# -*- coding: utf-8 -*-
"""Regression tests for the session SSE replay/live seam."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from functools import partial
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import uuid4

from fakeredis.aioredis import FakeRedis
from redis.asyncio.client import PubSub

from agentscope.app._bus_ops import publish_session_event
from agentscope.app._router import _session
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

    async def test_legacy_snapshot_retries_its_own_marker(self) -> None:
        """Replay idle history even if the first marker is lost."""
        bus = _LegacyBus()
        key = MessageBusKeys.session_events("s")
        history = {"frame": "history"}
        entry_id = await publish_session_event(bus, "s", history)
        publish = bus.publish
        markers = []

        async def lose_first_marker(key: str, payload: dict) -> None:
            markers.append(payload)
            if len(markers) == 1:
                payload = {MessageBusKeys.SESSION_BARRIER_FIELD: "other"}
            await publish(key, payload)

        response = await stream_session_events(
            session_id="s",
            agent_id="a",
            user_id="u",
            storage=_Storage(),  # type: ignore[arg-type]
            message_bus=bus,
            after=None,
        )
        with (
            patch.object(bus, "publish", side_effect=lose_first_marker),
            patch(
                "agentscope.app._router._session._HEARTBEAT_INTERVAL_SECS",
                0.02,
            ),
        ):
            async with aclosing(response.body_iterator) as stream:

                async def first_data() -> dict:
                    async for frame in stream:
                        if frame != ":\n\n":
                            return _decode(frame)
                    self.fail("Stream closed before replay")

                self.assertEqual(
                    await asyncio.wait_for(first_data(), 1),
                    history,
                )
        self.assertEqual(len(markers), 2)
        self.assertEqual(markers[0], markers[1])
        self.assertEqual(await bus.log_read(key), [(entry_id, history)])

    async def test_legacy_barrier_timeout_preserves_resume(self) -> None:
        """Reconnect after lost markers and replay the unconfirmed row."""
        async with _LegacyBus() as bus, asyncio.timeout(3):
            key = MessageBusKeys.session_events("s")
            first = {"frame": "first"}
            missed = {"frame": "missed"}
            cursor = await publish_session_event(bus, "s", first)

            connect = partial(
                stream_session_events,
                session_id="s",
                agent_id="a",
                user_id="u",
                storage=_Storage(),  # type: ignore[arg-type]
                message_bus=bus,
            )
            with patch.object(_session, "_HEARTBEAT_INTERVAL_SECS", 0.02):
                response = await connect(after=None)
                async with aclosing(response.body_iterator) as stream:
                    async for frame in stream:
                        if frame != ":\n\n":
                            self.assertEqual(_decode(frame), first)
                            break
                    else:
                        self.fail("Stream closed before the first frame")
                    await bus.log_append(key, missed)
                    with patch.object(bus, "publish") as drop:
                        remaining = [
                            frame async for frame in stream if frame != ":\n\n"
                        ]
                    self.assertEqual(remaining, [])
                    self.assertEqual(drop.await_count, 3)
            response = await connect(after=cursor)
            async with aclosing(response.body_iterator) as stream:
                self.assertEqual(_decode(await anext(stream)), missed)

    async def test_replay_and_resume_with_opaque_cursors(self) -> None:
        """Replay in append order and skip late duplicate notifications."""
        key = MessageBusKeys.session_events("s")
        first_heartbeat = asyncio.Event()
        publish = _LegacyBus.publish

        async def delay_marker(bus: _LegacyBus, key: str, value: dict) -> None:
            await first_heartbeat.wait()
            await publish(bus, key, value)

        async def read(
            key: str,
            since: str | None = None,
            max_count: int = 100,
        ) -> list[tuple[str, dict]]:
            self.assertEqual(key, MessageBusKeys.session_events("s"))
            ids = [entry_id for entry_id, _ in entries]
            start = 0 if since is None else ids.index(since) + 1
            return entries[start : start + min(1, max_count)]

        for after, first in ((None, 9), ("evt-9", 10), ("evt-10", 11)):
            entries = [
                ("evt-9", {"sequence": 9}),
                ("evt-10", {"sequence": 10}),
            ]
            with self.subTest(after=after), patch.object(
                _LegacyBus,
                "log_read",
                side_effect=read,
            ):
                async with _LegacyBus() as bus:
                    response = await stream_session_events(
                        session_id="s",
                        agent_id="a",
                        user_id="u",
                        storage=_Storage(),  # type: ignore[arg-type]
                        message_bus=bus,
                        after=after,
                    )
                    async with (
                        asyncio.timeout(3),
                        aclosing(response.body_iterator) as stream,
                    ):
                        with patch(
                            "agentscope.app._router._session."
                            "_HEARTBEAT_INTERVAL_SECS",
                            0.02,
                        ), patch.object(_LegacyBus, "publish", delay_marker):
                            frames = []
                            while (frame := await anext(stream)) != ":\n\n":
                                frames.append(_decode(frame))
                            first_heartbeat.set()
                        for entry_id, event in entries:
                            await bus.publish(
                                key,
                                {**event, "_entry_id": entry_id},
                            )
                        # Only the final log entry gets a live notification.
                        entries.extend(
                            [
                                ("evt-11", {"sequence": 11}),
                                ("evt-12", {"sequence": 12}),
                            ],
                        )
                        await bus.publish(
                            key,
                            {"sequence": 12, "_entry_id": "evt-12"},
                        )
                        await bus.publish(key, {"done": True})
                        async for frame in stream:
                            if frame == ":\n\n":
                                continue
                            event = _decode(frame)
                            if event == {"done": True}:
                                break
                            frames.append(event)
                        else:
                            self.fail("Stream closed before the live marker")
                self.assertEqual(
                    frames,
                    [{"sequence": n} for n in range(first, 13)],
                )

    async def test_redis_replay_with_resp2_and_resp3(self) -> None:
        """Both PING response formats release replay and allow live events."""
        for protocol in (2, 3):
            with self.subTest(protocol=protocol):
                async with FakeRedis(
                    protocol=protocol,
                    decode_responses=True,
                ) as redis:
                    async with RedisMessageBus(
                        connection_pool=redis.connection_pool,
                    ) as bus:
                        await self._assert_replay_then_live(bus)

    async def test_lost_redis_pong_during_replay(self) -> None:
        """Retry one lost PONG or close without delivering past the gap."""
        for drop_all in (False, True):
            with self.subTest(drop_all=drop_all):
                await self._check_lost_pong(drop_all)

    async def _check_lost_pong(self, drop_all: bool) -> None:
        async with (
            FakeRedis(protocol=3, decode_responses=True) as redis,
            RedisMessageBus(connection_pool=redis.connection_pool) as bus,
            asyncio.timeout(3),
        ):
            cursor = await publish_session_event(bus, "s", {"frame": "seen"})
            await publish_session_event(bus, "s", {"frame": "history"})
            handle = PubSub.handle_message
            markers = []

            async def lose_pong(
                sub: PubSub,
                response: object,
                ignore_subscribe_messages: bool = False,
            ) -> dict | None:
                event = await handle(sub, response, ignore_subscribe_messages)
                if event and event["type"] == "pong":
                    markers.append(event["data"])
                    if len(markers) == 1:
                        await publish_session_event(bus, "s", {"frame": "new"})
                        return None
                    if drop_all:
                        return None
                return event

            connect = partial(
                stream_session_events,
                session_id="s",
                agent_id="a",
                user_id="u",
                storage=_Storage(),  # type: ignore[arg-type]
                message_bus=bus,
                after=cursor,
            )
            response = await connect()
            frames = []
            with (
                patch.object(PubSub, "handle_message", lose_pong),
                patch.object(bus, "_BARRIER_ATTEMPT_TIMEOUT_SECS", 0.08),
                patch.object(_session, "_HEARTBEAT_INTERVAL_SECS", 0.02),
            ):
                async with aclosing(response.body_iterator) as stream:
                    async for frame in stream:
                        if frame != ":\n\n":
                            frames.append(_decode(frame))
                            if frames[-1] == {"frame": "new"}:
                                break
            self.assertEqual(
                frames,
                [] if drop_all else [{"frame": "history"}, {"frame": "new"}],
            )
            self.assertEqual(len(set(markers)), 3 if drop_all else 2)
            if drop_all:
                response = await connect()
                async with aclosing(response.body_iterator) as stream:
                    self.assertEqual(
                        [_decode(await anext(stream)) for _ in range(2)],
                        [{"frame": "history"}, {"frame": "new"}],
                    )

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
        async with InMemoryMessageBus() as bus:
            await self._assert_replay_then_live(bus)

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

    async def test_checkpoint_trims_snapshot_before_delivery(self) -> None:
        """Keep a queued delta before an end trimmed by a later checkpoint."""
        bus = InMemoryMessageBus()
        old = await publish_session_event(bus, "s", {"frame": "old"})
        after = await publish_session_event(bus, "s", {"frame": "checkpoint"})
        read = bus.log_read
        injected = False

        async def read_between_checkpoints(
            key: str,
            since: str | None = None,
            max_count: int = 100,
        ) -> list[tuple[str, dict]]:
            nonlocal injected
            if injected:
                return await read(key, since, max_count)
            injected = True
            await bus.publish(key, {"frame": "old", "_entry_id": old})
            await publish_session_event(bus, "s", {"frame": "delta"})
            end = await publish_session_event(bus, "s", {"frame": "end"})
            await bus.log_trim(key, end)
            snapshot = await read(key, since, max_count)
            tail = await publish_session_event(bus, "s", {"frame": "tail"})
            await bus.log_trim(key, tail)
            return snapshot

        response = await stream_session_events(
            session_id="s",
            agent_id="a",
            user_id="u",
            storage=_Storage(),  # type: ignore[arg-type]
            message_bus=bus,
            after=after,
        )
        async with aclosing(response.body_iterator) as stream:
            with patch.object(bus, "log_read", read_between_checkpoints):
                frames = [
                    _decode(await asyncio.wait_for(anext(stream), 1))
                    for _ in range(3)
                ]
        self.assertEqual(
            frames,
            [{"frame": f} for f in ("delta", "end", "tail")],
        )

    async def test_snapshot_ahead_of_subscription_feeder(self) -> None:
        """A delta in the transport buffer must precede the snapshot end."""
        for bus_type in (InMemoryMessageBus, _LegacyBus):
            for resume in (False, True):
                with self.subTest(bus=bus_type.__name__, resume=resume):
                    async with bus_type() as bus:
                        await self._assert_snapshot_ahead_of_feeder(
                            bus,
                            resume,
                        )

    async def _assert_snapshot_ahead_of_feeder(
        self,
        bus: MessageBus,
        resume: bool,
    ) -> None:
        after = None
        if resume:
            after = await publish_session_event(bus, "s", {"frame": "prior"})
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
            after=after,
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
                async with (
                    asyncio.timeout(2),
                    aclosing(response.body_iterator) as stream,
                ):
                    # Establish the subscription before dropping a broadcast.
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
                    frames = []
                    while len(frames) < len(expected):
                        frame = await anext(stream)
                        if frame != ":\n\n":
                            frames.append(_decode(frame))
                    self.assertEqual(frames, expected)
                    self.assertEqual(await anext(stream), ":\n\n")
