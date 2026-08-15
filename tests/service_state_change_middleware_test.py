# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for state-change event payloads."""
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Callable
from unittest import IsolatedAsyncioTestCase

from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.message_bus._keys import MessageBusKeys
from agentscope.app.middleware import StateChangeMiddleware
from agentscope.permission import PermissionMode
from agentscope.state import AgentState, Task


async def _drain(generator: AsyncGenerator) -> list:
    """Collect all values yielded by an async generator."""
    return [item async for item in generator]


class StateChangeMiddlewareTest(IsolatedAsyncioTestCase):
    """Verify ``state_updated`` carries only changed fields."""

    async def asyncSetUp(self) -> None:
        """Create a fresh agent, bus, and middleware."""
        self.bus = InMemoryMessageBus()
        self.agent = SimpleNamespace(state=AgentState())
        self.session_id = "session-id"
        self.middleware = StateChangeMiddleware(
            self.bus,
            self.session_id,
        )

    async def _run_reply(self, mutate: Callable[[], None]) -> list[dict]:
        """Run a reply handler and return its published events."""

        async def next_handler(**_kwargs: Any) -> AsyncGenerator:
            mutate()
            yield "downstream"

        output = await _drain(
            self.middleware.on_reply(
                self.agent,
                {},
                next_handler,
            ),
        )
        self.assertEqual(output, ["downstream"])
        entries = await self.bus.log_read(
            MessageBusKeys.session_events(self.session_id),
        )
        return [payload for _, payload in entries]

    async def test_task_change_omits_permission_context(self) -> None:
        """A task-only update should not look like a permission update."""

        def mutate() -> None:
            self.agent.state.tasks_context.tasks.append(
                Task(
                    subject="Inspect logs",
                    description="Find the root cause",
                    metadata={},
                ),
            )

        events = await self._run_reply(mutate)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "state_updated")
        self.assertIn("tasks_context", events[0]["value"])
        self.assertNotIn("permission_context", events[0]["value"])

    async def test_permission_change_omits_tasks_context(self) -> None:
        """A permission-only update should not look like a task update."""

        def mutate() -> None:
            self.agent.state.permission_context.mode = PermissionMode.BYPASS

        events = await self._run_reply(mutate)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "state_updated")
        self.assertIn("permission_context", events[0]["value"])
        self.assertNotIn("tasks_context", events[0]["value"])

    async def test_unchanged_state_publishes_nothing(self) -> None:
        """A reply with no tracked change should not emit an event."""
        events = await self._run_reply(lambda: None)

        self.assertEqual(events, [])
