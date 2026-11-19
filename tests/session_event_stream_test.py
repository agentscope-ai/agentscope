# -*- coding: utf-8 -*-
"""Regression tests for the session SSE replay/live seam."""

import asyncio
from contextlib import aclosing
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app._bus_ops import publish_session_event
from agentscope.app._router._session import stream_session_events
from agentscope.app.message_bus import InMemoryMessageBus


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
