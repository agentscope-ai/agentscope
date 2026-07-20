# -*- coding: utf-8 -*-
"""Unit tests for the runtime state injection of the agent, i.e. the
``Agent._inject_runtime_state`` method."""
from datetime import datetime, tzinfo
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch


from utils import AnyString, MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.message import HintBlock
from agentscope.state import Task
from agentscope.tool import Toolkit


# The fixed source used by the agent to mark its own runtime-state injection.
INJECTION_SOURCE = '{"label": "System", "sublabel": "Runtime State"}'

# The frozen "now" used across the tests, so time-related assertions are
# deterministic.
FROZEN_NOW = datetime(2026, 7, 1, 12, 0, 0)


class _FrozenDatetime(datetime):
    """A ``datetime`` subclass whose ``now`` always returns a fixed instant,
    while keeping the other classmethods (``strptime``/``strftime``) intact."""

    @classmethod
    def now(  # type: ignore[override]
        cls,
        tz: tzinfo | None = None,
    ) -> datetime:
        """Return the frozen instant (optionally attached with ``tz``)."""
        if tz is not None:
            return FROZEN_NOW.replace(tzinfo=tz)
        return FROZEN_NOW


class AgentInjectionTest(IsolatedAsyncioTestCase):
    """Test cases for the runtime state injection."""

    async def asyncSetUp(self) -> None:
        """Create a fresh agent with a mock model for each test."""
        self.model = MockModel(context_size=1000)
        self.agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=self.model,
            toolkit=Toolkit(),
            injection_config=InjectionConfig(),
        )
        self.agent.state.reply_id = "reply-1"
        self.agent.state.cur_iter = 0

    # ------------------------------------------------------------------ utils
    async def _run_injection(self) -> list:
        """Drive the async generator and collect the yielded events."""
        return [
            # pylint: disable=protected-access
            evt
            async for evt in self.agent._inject_runtime_state()
        ]

    def _add_injection(self, time_str: str) -> None:
        """Append an existing runtime-state injection carrying ``time_str``."""
        self.agent.state.append_context(
            self.agent.name,
            [
                HintBlock(
                    source=INJECTION_SOURCE,
                    hint=(
                        f"<current-time>{time_str}</current-time>\n"
                        "<timezone>UTC</timezone>"
                    ),
                ),
            ],
        )

    @staticmethod
    def _expected_event(hint: str, reply_id: str = "reply-1") -> dict:
        """Build the expected ``HintBlockEvent`` dump for the given hint."""
        return {
            "id": AnyString(),
            "created_at": AnyString(),
            "metadata": {},
            "type": "HINT_BLOCK",
            "reply_id": reply_id,
            "block_id": AnyString(),
            "source": INJECTION_SOURCE,
            "hint": hint,
        }

    @staticmethod
    def _expected_hint_block(hint: str) -> dict:
        """Build the expected persisted ``HintBlock`` dump."""
        return {
            "type": "hint",
            "hint": hint,
            "id": AnyString(),
            "source": INJECTION_SOURCE,
        }

    # ------------------------------------------------------------------ tests
    async def test_first_reply_triggers_time_injection(self) -> None:
        """The first reply (empty context) should trigger a time injection."""
        expected_hint = (
            "<current-time>2026-07-01T12:00:00</current-time>\n"
            "<timezone>UTC</timezone>"
        )
        with patch("agentscope.agent._agent.datetime", _FrozenDatetime):
            events = await self._run_injection()

        self.assertEqual(
            [self._expected_event(expected_hint)],
            [evt.model_dump() for evt in events],
        )
        self.assertEqual(
            [self._expected_hint_block(expected_hint)],
            [_.model_dump() for _ in self.agent.state.context[-1].content],
        )

    async def test_long_interval_triggers_time_injection(self) -> None:
        """A stale last injection (long elapsed time) should re-inject, while a
        recent one should not."""
        expected_hint = (
            "<current-time>2026-07-01T12:00:00</current-time>\n"
            "<timezone>UTC</timezone>"
        )
        # Avoid the context-length branch, which only runs on the first iter.
        self.agent.state.cur_iter = 1

        # Case 1: last injection was 6 hours ago -> re-inject.
        self._add_injection("2026-07-01T06:00:00")
        with patch("agentscope.agent._agent.datetime", _FrozenDatetime):
            events = await self._run_injection()
        self.assertEqual(
            [self._expected_event(expected_hint)],
            [evt.model_dump() for evt in events],
        )

        # Case 2: last injection was 10 minutes ago (< time_interval) -> skip.
        self.agent.state.context = []
        self._add_injection("2026-07-01T11:50:00")
        with patch("agentscope.agent._agent.datetime", _FrozenDatetime):
            events = await self._run_injection()
        self.assertEqual([], events)

    async def test_injection_after_compression(self) -> None:
        """A recent injection should not re-inject, but once the context is
        compressed away, the next call should inject again."""
        expected_hint = (
            "<current-time>2026-07-01T12:00:00</current-time>\n"
            "<timezone>UTC</timezone>"
        )
        self.agent.state.cur_iter = 1

        # There is a recent injection before compression -> no new injection.
        self._add_injection("2026-07-01T12:00:00")
        with patch("agentscope.agent._agent.datetime", _FrozenDatetime):
            events = await self._run_injection()
        self.assertEqual([], events)

        # Simulate a compression that drops the old context (and injection).
        self.agent.state.context = []
        self.agent.state.summary = "A summary of the previous work."
        with patch("agentscope.agent._agent.datetime", _FrozenDatetime):
            events = await self._run_injection()
        self.assertEqual(
            [self._expected_event(expected_hint)],
            [evt.model_dump() for evt in events],
        )

    async def test_pending_task_triggers_injection(self) -> None:
        """Pending tasks without task-related tool calls in the context should
        trigger a tasks injection."""
        expected_hint = (
            "<system-reminder>Treat the following as current ground truth:\n"
            "<current-session>You're in a conversation with session ID: "
            f"{self.agent.state.session_id}</current-session>\n",
            "<tasks>You have 0 in-progress tasks and 1 pending tasks. "
            "Use `TaskList` to view them if you don't know.</tasks>"
            "</system-reminder>",
        )
        self.agent.state.cur_iter = 1
        # A recent injection so the time branch is not triggered.
        self._add_injection("2026-07-01T12:00:00")
        self.agent.state.tasks_context.tasks = [
            Task(
                subject="Write the report",
                description="Draft the quarterly report.",
                metadata={},
                state="pending",
            ),
        ]
        with patch("agentscope.agent._agent.datetime", _FrozenDatetime):
            events = await self._run_injection()

        self.assertEqual(
            [self._expected_event(expected_hint)],
            [evt.model_dump() for evt in events],
        )

    async def test_context_size_triggers_injection(self) -> None:
        """When the input tokens are close to the compression threshold, a
        context-length injection should be triggered."""
        expected_hint = (
            "<context-length>Your current context contains 700 tokens. "
            "When reaching 800.0 tokens, your context will be compressed."
            "</context-length>"
        )
        # First iteration is required for the context-length branch.
        self.agent.state.cur_iter = 0
        # A recent injection so the time branch is not triggered.
        self._add_injection("2026-07-01T12:00:00")
        # 700 > max(0, 0.8 - 0.2) * 1000 == 600 -> triggers the injection.
        self.model.count_tokens = AsyncMock(return_value=700)

        with patch("agentscope.agent._agent.datetime", _FrozenDatetime):
            events = await self._run_injection()

        self.assertEqual(
            [self._expected_event(expected_hint)],
            [evt.model_dump() for evt in events],
        )
