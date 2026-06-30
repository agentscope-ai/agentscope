# -*- coding: utf-8 -*-
"""Tests for app state-change notifications."""
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest import IsolatedAsyncioTestCase

from agentscope.app.middleware import StateChangeMiddleware
from agentscope.state import AgentState


class _FakeBus:
    """Capture events published through ``publish_session_event``."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_append(
        self,
        _key: str,
        event: dict,
        max_len: int | None = None,
    ) -> str:
        """Store one replay event."""
        del max_len
        self.events.append(event)
        return "1-0"

    async def publish(self, _key: str, _event: dict) -> None:
        """Ignore live fan-out in this focused test."""


class StateChangeMiddlewareTest(IsolatedAsyncioTestCase):
    """Context usage changes should reach the session event stream."""

    async def test_reply_publishes_context_usage(self) -> None:
        """A model-turn usage update emits authoritative session state."""
        bus = _FakeBus()
        middleware = StateChangeMiddleware(bus, "session-1")
        agent = SimpleNamespace(state=AgentState())

        async def reply(**_kwargs: object) -> AsyncGenerator[str, None]:
            agent.state.context_usage.current_tokens = 42
            agent.state.context_usage.compression_threshold_tokens = 80
            agent.state.context_usage.context_window_tokens = 100
            agent.state.context_usage.trigger_ratio = 0.8
            yield "event"

        result = [
            item
            async for item in middleware.on_reply(
                agent,
                {},
                reply,
            )
        ]

        self.assertEqual(result, ["event"])
        self.assertEqual(len(bus.events), 1)
        self.assertEqual(bus.events[0]["name"], "state_updated")
        self.assertEqual(
            bus.events[0]["value"]["context_usage"],
            {
                "current_tokens": 42,
                "compression_threshold_tokens": 80,
                "context_window_tokens": 100,
                "trigger_ratio": 0.8,
            },
        )
