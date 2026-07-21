# -*- coding: utf-8 -*-
"""Unit tests for the runtime state injection of the agent, i.e. the
``Agent._inject_runtime_state`` method."""
from datetime import datetime, tzinfo
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

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

        # Freeze the wall-clock time for all the test cases
        patcher = patch("agentscope.agent._agent.datetime", _FrozenDatetime)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------ utils
    async def _run_injection(self) -> list:
        """Drive the async generator and collect the yielded events."""
        return [
            # pylint: disable=protected-access
            evt
            async for evt in self.agent._inject_runtime_state()
        ]

    def _add_injection(self, time_str: str, timezone: str = "UTC") -> None:
        """Append an existing runtime-state injection carrying ``time_str``,
        which is the wall-clock time of ``timezone``."""
        self.agent.state.append_context(
            self.agent.name,
            [
                HintBlock(
                    source=INJECTION_SOURCE,
                    hint=(
                        f"<current-time>{time_str}</current-time>\n"
                        f"<timezone>{timezone}</timezone>"
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
            "<system-reminder>Treat the following as the ground truth at this "
            "point of the conversation. Anything stated earlier is outdated, "
            "and a later reminder, if any, supersedes this one:\n"
            "<current-time>2026-07-01T12:00:00</current-time>\n"
            "<timezone>UTC</timezone>\n"
            "</system-reminder>"
        )
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
            "<system-reminder>Treat the following as the ground truth at this "
            "point of the conversation. Anything stated earlier is outdated, "
            "and a later reminder, if any, supersedes this one:\n"
            "<current-time>2026-07-01T12:00:00</current-time>\n"
            "<timezone>UTC</timezone>\n"
            "</system-reminder>"
        )
        # Avoid the context-length branch, which only runs on the first iter.
        self.agent.state.cur_iter = 1

        # Case 1: last injection was 6 hours ago -> re-inject.
        self._add_injection("2026-07-01T06:00:00")
        events = await self._run_injection()
        self.assertEqual(
            [self._expected_event(expected_hint)],
            [evt.model_dump() for evt in events],
        )

        # Case 2: last injection was 10 minutes ago (< time_interval) -> skip.
        self.agent.state.context = []
        self._add_injection("2026-07-01T11:50:00")
        events = await self._run_injection()
        self.assertEqual([], events)

    async def test_injection_after_compression(self) -> None:
        """A recent injection should not re-inject, but once the context is
        compressed away, the next call should inject again."""
        expected_hint = (
            "<system-reminder>Treat the following as the ground truth at this "
            "point of the conversation. Anything stated earlier is outdated, "
            "and a later reminder, if any, supersedes this one:\n"
            "<current-time>2026-07-01T12:00:00</current-time>\n"
            "<timezone>UTC</timezone>\n"
            "</system-reminder>"
        )
        self.agent.state.cur_iter = 1

        # There is a recent injection before compression -> no new injection.
        self._add_injection("2026-07-01T12:00:00")
        events = await self._run_injection()
        self.assertEqual([], events)

        # Simulate a compression that drops the old context (and injection).
        self.agent.state.context = []
        self.agent.state.summary = "A summary of the previous work."
        events = await self._run_injection()
        self.assertEqual(
            [self._expected_event(expected_hint)],
            [evt.model_dump() for evt in events],
        )

    async def test_pending_task_triggers_injection(self) -> None:
        """Pending tasks without task-related tool calls in the context should
        trigger a tasks injection."""
        expected_hint = (
            "<system-reminder>Treat the following as the ground truth at this "
            "point of the conversation. Anything stated earlier is outdated, "
            "and a later reminder, if any, supersedes this one:\n"
            "<tasks>You have 0 in-progress tasks and 1 pending tasks. "
            "Use `TaskList` to view them if you don't know.</tasks>\n"
            "</system-reminder>"
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
        events = await self._run_injection()

        self.assertEqual(
            [self._expected_event(expected_hint)],
            [evt.model_dump() for evt in events],
        )

        # The tasks reminder is already in the context -> no repeated injection
        events = await self._run_injection()
        self.assertEqual([], events)

    async def test_recorded_timezone_is_honored(self) -> None:
        """The recorded timezone should be used to restore the recorded time,
        so a changed ``timezone`` config doesn't distort the elapsed time."""
        self.agent.state.cur_iter = 1

        # The frozen now is 12:00 UTC, i.e. 20:00 in Shanghai. An injection
        # recorded 10 minutes ago in Shanghai -> within the interval, skip.
        self._add_injection("2026-07-01T19:50:00", timezone="Asia/Shanghai")
        events = await self._run_injection()
        self.assertEqual([], events)

        # The same wall-clock time read as UTC would be 7h50m in the future,
        # so a negative elapsed time must trigger an injection instead of
        # being silently swallowed.
        self.agent.state.context = []
        self._add_injection("2026-07-01T19:50:00")
        events = await self._run_injection()
        self.assertEqual(1, len(events))

    async def test_extra_fields_are_attached(self) -> None:
        """The extra fields should be attached to a triggered injection."""
        expected_hint = (
            "<system-reminder>Treat the following as the ground truth at this "
            "point of the conversation. Anything stated earlier is outdated, "
            "and a later reminder, if any, supersedes this one:\n"
            "<current-time>2026-07-01T12:00:00</current-time>\n"
            "<timezone>UTC</timezone>\n"
            "<workspace>/home/friday</workspace>\n"
            "</system-reminder>"
        )
        self.agent.injection_config = InjectionConfig(
            extra_fields={"workspace": "/home/friday"},
        )
        events = await self._run_injection()

        self.assertEqual(
            [self._expected_event(expected_hint)],
            [evt.model_dump() for evt in events],
        )

    async def test_extra_fields_do_not_trigger_injection(self) -> None:
        """The extra fields alone should not trigger an injection."""
        self.agent.injection_config = InjectionConfig(
            extra_fields={"workspace": "/home/friday"},
        )
        self.agent.state.cur_iter = 1
        # A recent injection so the time branch is not triggered.
        self._add_injection("2026-07-01T12:00:00")
        events = await self._run_injection()

        self.assertEqual([], events)

    async def test_disabled_injection(self) -> None:
        """Nothing should be injected when the injection is turned off."""
        self.agent.injection_config = InjectionConfig(
            inject_runtime_state=False,
        )
        events = await self._run_injection()

        self.assertEqual([], events)
        self.assertEqual([], self.agent.state.context)

    async def test_context_size_triggers_injection(self) -> None:
        """When the input tokens are close to the compression threshold, a
        context-length injection should be triggered."""
        expected_hint = (
            "<system-reminder>Treat the following as the ground truth at this "
            "point of the conversation. Anything stated earlier is outdated, "
            "and a later reminder, if any, supersedes this one:\n"
            "<context-length>Your current context contains 700 tokens. "
            "When reaching 800 tokens, your context will be compressed."
            "</context-length>\n"
            "</system-reminder>"
        )
        # First iteration is required for the context-length branch.
        self.agent.state.cur_iter = 0
        # A recent injection so the time branch is not triggered.
        self._add_injection("2026-07-01T12:00:00")
        # 700 > max(0, 0.8 - 0.2) * 1000 == 600 -> triggers the injection.
        self.model.count_tokens = AsyncMock(return_value=700)

        events = await self._run_injection()

        self.assertEqual(
            [self._expected_event(expected_hint)],
            [evt.model_dump() for evt in events],
        )

    async def test_context_size_is_independent_of_the_other_fields(
        self,
    ) -> None:
        """The context length should be reported even when the other
        dimensions are triggered in the same injection."""
        expected_hint = (
            "<system-reminder>Treat the following as the ground truth at this "
            "point of the conversation. Anything stated earlier is outdated, "
            "and a later reminder, if any, supersedes this one:\n"
            "<current-time>2026-07-01T12:00:00</current-time>\n"
            "<timezone>UTC</timezone>\n"
            "<context-length>Your current context contains 700 tokens. "
            "When reaching 800 tokens, your context will be compressed."
            "</context-length>\n"
            "</system-reminder>"
        )
        # The first reply, where the time injection is always triggered.
        self.agent.state.cur_iter = 0
        self.model.count_tokens = AsyncMock(return_value=700)

        events = await self._run_injection()

        self.assertEqual(
            [self._expected_event(expected_hint)],
            [evt.model_dump() for evt in events],
        )

    async def test_template_without_placeholder_is_rejected(self) -> None:
        """A template that would silently drop the injected fields should be
        rejected at the config level."""
        with self.assertRaises(ValidationError):
            InjectionConfig(template="<system-reminder></system-reminder>")

    async def test_template_with_curly_braces_is_kept(self) -> None:
        """The curly braces other than the placeholder should survive."""
        self.agent.injection_config = InjectionConfig(
            template='{"reminder": "{runtime_state}"}',
        )
        events = await self._run_injection()

        self.assertEqual(
            [
                self._expected_event(
                    '{"reminder": "'
                    "<current-time>2026-07-01T12:00:00</current-time>\n"
                    "<timezone>UTC</timezone>"
                    '"}',
                ),
            ],
            [evt.model_dump() for evt in events],
        )

    async def test_invalid_timezone_falls_back_to_utc(self) -> None:
        """An unresolvable timezone shouldn't break the reply loop."""
        expected_hint = (
            "<system-reminder>Treat the following as the ground truth at this "
            "point of the conversation. Anything stated earlier is outdated, "
            "and a later reminder, if any, supersedes this one:\n"
            "<current-time>2026-07-01T12:00:00</current-time>\n"
            "<timezone>Mars/Olympus_Mons</timezone>\n"
            "</system-reminder>"
        )
        self.agent.injection_config = InjectionConfig(
            timezone="Mars/Olympus_Mons",
        )
        events = await self._run_injection()

        self.assertEqual(
            [self._expected_event(expected_hint)],
            [evt.model_dump() for evt in events],
        )
