# -*- coding: utf-8 -*-
"""Regression tests for the session SSE replay/live handoff."""
# pylint: disable=missing-class-docstring,missing-function-docstring
import asyncio
import json
from unittest import IsolatedAsyncioTestCase

from agentscope.app._bus_ops import publish_session_event
from agentscope.app._router._session import stream_session_events
from agentscope.app._service import SessionProjection, SubagentHitlProjector
from agentscope.app.message_bus import InMemoryMessageBus


def _data(frame: str) -> dict:
    line = next(x for x in frame.splitlines() if x.startswith("data: "))
    return json.loads(line[6:])


class _Storage:
    def __init__(self) -> None:
        self.lookup_started = asyncio.Event()
        self.release_lookup = asyncio.Event()

    async def get_session(self, *_args: str) -> object | None:
        if _args[-1] == "leader":
            return object()
        self.lookup_started.set()
        await self.release_lookup.wait()
        return None


class _OverlapBus(InMemoryMessageBus):
    injected = False

    async def log_read(self, key, since=None, max_count=100):  # type: ignore
        if not self.injected:
            self.injected = True
            await publish_session_event(self, "leader", {"n": "overlap"})
        return await super().log_read(key, since, max_count)


class _NeverReadyBus(InMemoryMessageBus):
    async def subscribe(self, key, *, on_ready=None):  # type: ignore
        await asyncio.Event().wait()
        yield {}  # pragma: no cover


class TestSessionEventHandoff(IsolatedAsyncioTestCase):
    async def test_projection_window_is_lossless_and_overlap_is_deduped(
        self,
    ) -> None:
        bus = _OverlapBus()
        storage = _Storage()
        payload = {
            "worker_session_id": "worker",
            "worker_agent_id": "worker-agent",
            "worker_agent_name": "worker",
            "reply_id": "reply",
            "event_type": "require_user_confirm",
            "event": {},
            "created_at": "now",
        }
        await SessionProjection(bus).upsert(
            "leader",
            SubagentHitlProjector.KIND,
            SubagentHitlProjector.entry_id("worker", "reply"),
            payload,
        )
        response = await stream_session_events(
            "leader",
            agent_id="agent",
            user_id="user",
            storage=storage,
            message_bus=bus,
        )
        iterator = response.body_iterator
        try:
            replay = await asyncio.wait_for(anext(iterator), 1)
            live_task = asyncio.create_task(anext(iterator))
            await asyncio.wait_for(storage.lookup_started.wait(), 1)
            await publish_session_event(bus, "leader", {"n": "projection"})
            storage.release_lookup.set()
            live = await asyncio.wait_for(live_task, 1)

            self.assertEqual(_data(replay), {"n": "overlap"})
            self.assertEqual(_data(live), {"n": "projection"})
        finally:
            await iterator.aclose()
            await bus.aclose()

    async def test_cancel_before_subscribe_ready_cleans_feeder(self) -> None:
        bus = _NeverReadyBus()
        response = await stream_session_events(
            "leader",
            agent_id="agent",
            user_id="user",
            storage=_Storage(),
            message_bus=bus,
        )
        iterator = response.body_iterator
        pending = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        await iterator.aclose()
        tasks = asyncio.all_tasks()
        self.assertFalse(
            any(t.get_name() == "sse-feeder:leader" for t in tasks),
        )
        await bus.aclose()
