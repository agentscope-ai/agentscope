# -*- coding: utf-8 -*-
"""Regression tests for the session SSE replay/live seam."""

import asyncio
from contextlib import aclosing
import json
from unittest import IsolatedAsyncioTestCase

from agentscope.app._bus_ops import publish_session_event
from agentscope.app._router._session import (
    _worker_still_asking,
    stream_session_events,
)
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.message import AssistantMsg, ToolCallBlock, ToolCallState
from agentscope.state import AgentState


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

    async def test_replay_validates_each_projected_tool_call(self) -> None:
        """A sibling pending call must not keep an answered card alive."""
        session = type("Session", (), {})()
        session.state = AgentState(
            context=[
                AssistantMsg(
                    name="worker",
                    id="reply-1",
                    content=[
                        ToolCallBlock(
                            id="answered",
                            name="tool",
                            input="{}",
                            state=ToolCallState.FINISHED,
                        ),
                        ToolCallBlock(
                            id="pending",
                            name="tool",
                            input="{}",
                            state=ToolCallState.ASKING,
                        ),
                    ],
                ),
            ],
        )

        class _WorkerStorage:
            async def get_session(self, *_: object) -> object:
                return session

        self.assertFalse(
            await _worker_still_asking(
                _WorkerStorage(),  # type: ignore[arg-type]
                "u-1",
                "a-1",
                "s-1",
                "reply-1",
                "answered",
            ),
        )
        self.assertTrue(
            await _worker_still_asking(
                _WorkerStorage(),  # type: ignore[arg-type]
                "u-1",
                "a-1",
                "s-1",
                "reply-1",
                "pending",
            ),
        )
