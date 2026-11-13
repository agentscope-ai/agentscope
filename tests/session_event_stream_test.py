# -*- coding: utf-8 -*-
"""Regression tests for the session SSE replay/live seam and error propagation."""

import asyncio
from contextlib import aclosing
import json
from typing import AsyncGenerator, Callable
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app._bus_ops import publish_session_event
from agentscope.app._router._session import stream_session_events
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys


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


class _BrokenSubscriptionBus(InMemoryMessageBus):
    """A bus whose live subscription fails on demand."""

    def __init__(self, fail_before_ready: bool = False) -> None:
        super().__init__()
        self.fail_before_ready = fail_before_ready
        self.fail = asyncio.Event()
        self.events: list[dict] = []

    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Raise before or after reporting that the subscription is live."""
        if self.fail_before_ready:
            raise RuntimeError("subscription failed")
        if callable(on_ready):
            on_ready()
        for event in self.events:
            yield event
        await self.fail.wait()
        raise RuntimeError("subscription failed")


def _decode(frame: str | bytes) -> dict:
    """Decode one SSE data frame."""
    if isinstance(frame, bytes):
        frame = frame.decode()
    return json.loads(frame.removeprefix("data: ").strip())


class SessionEventStreamTest(IsolatedAsyncioTestCase):
    """The session route changes from replay to live without a gap."""

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

    async def test_subscription_failure_before_ready_is_propagated(
        self,
    ) -> None:
        """Opening fails immediately if the subscription never comes up."""
        bus = _BrokenSubscriptionBus(fail_before_ready=True)
        response = await stream_session_events(
            session_id="s-1",
            agent_id="a-1",
            user_id="u-1",
            storage=_Storage(),  # type: ignore[arg-type]
            message_bus=bus,
            after=None,
        )

        with self.assertRaisesRegex(RuntimeError, "subscription failed"):
            async with aclosing(response.body_iterator) as stream:
                await anext(stream)

    async def test_subscription_failure_while_reading_is_propagated(
        self,
    ) -> None:
        """A lost live subscription does not leave the reader hanging or silently closed."""
        bus = _BrokenSubscriptionBus()
        response = await stream_session_events(
            session_id="s-1",
            agent_id="a-1",
            user_id="u-1",
            storage=_Storage(),  # type: ignore[arg-type]
            message_bus=bus,
            after=None,
        )

        async with aclosing(response.body_iterator) as stream:
            next_event = asyncio.create_task(anext(stream))
            bus.fail.set()
            with self.assertRaisesRegex(RuntimeError, "subscription failed"):
                await asyncio.wait_for(next_event, timeout=0.5)

    async def test_setup_timeout_does_not_wait_for_bus_cleanup(self) -> None:
        """Opening times out while subscription cleanup is pending."""
        bus = InMemoryMessageBus()
        release = asyncio.Event()
        closed = asyncio.Event()

        async def subscription(
            *_: object,
            **__: object,
        ) -> AsyncGenerator[dict, None]:
            try:
                await asyncio.Event().wait()
                yield {}
            finally:
                await release.wait()
                closed.set()

        with (
            patch.object(bus, "subscribe", side_effect=subscription),
            patch("agentscope.app._router._session._SUBSCRIBE_TIMEOUT_SECS", 0),
        ):
            response = await stream_session_events(
                session_id="s-1",
                agent_id="a-1",
                user_id="u-1",
                storage=_Storage(),  # type: ignore[arg-type]
                message_bus=bus,
                after=None,
            )
            async with aclosing(response.body_iterator) as stream:
                with self.assertRaisesRegex(TimeoutError, "bus subscription"):
                    await anext(stream)
                self.assertFalse(closed.is_set())
            release.set()
            await asyncio.wait_for(closed.wait(), timeout=1)

    async def test_live_events_deduplicated_by_entry_id(self) -> None:
        """Live events arriving multiple times are deduplicated."""
        bus = InMemoryMessageBus()
        await publish_session_event(bus, "s-1", {"sequence": 0})
        response = await stream_session_events(
            session_id="s-1",
            agent_id="a-1",
            user_id="u-1",
            storage=_Storage(),  # type: ignore[arg-type]
            message_bus=bus,
            after=None,
        )
        async with aclosing(response.body_iterator) as stream:
            zero = _decode(await anext(stream))
            self.assertEqual(zero, {"sequence": 0})

            key = MessageBusKeys.session_events("s-1")
            await bus.publish(key, {"sequence": 1, "_entry_id": "dup-1"})
            await bus.publish(key, {"sequence": 1, "_entry_id": "dup-1"})
            await bus.publish(key, {"sequence": 2, "_entry_id": "unique-2"})

            first = _decode(await asyncio.wait_for(anext(stream), timeout=1))
            second = _decode(await asyncio.wait_for(anext(stream), timeout=1))

            self.assertEqual(first, {"sequence": 1})
            self.assertEqual(second, {"sequence": 2})
