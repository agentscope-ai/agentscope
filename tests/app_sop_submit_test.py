# -*- coding: utf-8 -*-
"""Unittests for :class:`SubmitVerdict`'s attempt accounting.

Driven against an in-memory stand-in for storage: what is under test is
how many attempts one submission costs the run, not the write path.
"""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.app._tool._sop_submit import SubmitVerdict
from agentscope.message import TextBlock
from agentscope.sop import SOPPhase, SOPRunState, SOPStepRunState


class _Storage:
    """Holds one run, and hands back the same state object every read."""

    def __init__(self, state: SOPRunState) -> None:
        """Keep the run under a name the tool can ask for."""
        self.state = state
        self.writes = 0

    async def get_sop_run(  # pylint: disable=unused-argument
        self,
        user_id: str,
        run_id: str,
    ) -> Any:
        """Return the run as the tool expects it."""
        return type("Record", (), {"state": self.state})()

    async def update_sop_run(  # pylint: disable=unused-argument
        self,
        user_id: str,
        run_id: str,
        state: SOPRunState,
    ) -> None:
        """Take the written-back state."""
        self.state = state
        self.writes += 1


class SubmitVerdictTest(IsolatedAsyncioTestCase):
    """A verdict costs the attempt it judged, and only that one."""

    def _tool(self, storage: _Storage, state: SOPRunState) -> SubmitVerdict:
        """A verdict tool bound to the single step of *state*."""
        return SubmitVerdict(
            storage=storage,
            user_id="user-1",
            sop_run_id=state.id,
            step_index=0,
            verifier="reviewer",
        )

    @staticmethod
    def _state() -> SOPRunState:
        """A run whose one step has a submission waiting to be judged."""
        return SOPRunState(
            steps=[
                SOPStepRunState(
                    phase=SOPPhase.RUNNING,
                    submission=[TextBlock(text="did A")],
                ),
            ],
        )

    async def test_filing_the_same_verdict_twice_costs_one_attempt(
        self,
    ) -> None:
        """The middleware sends a turn back after a later tool call; the
        reviewer files again, and the run must not charge it twice."""
        state = self._state()
        storage = _Storage(state)
        tool = self._tool(storage, state)

        await tool(passed=False, message="no")
        await tool(passed=False, message="no")

        self.assertEqual(len(storage.state.steps[0].verifications), 1)
        self.assertEqual(
            storage.state.steps[0].verifications[0].message,
            "no",
        )

    async def test_accepting_twice_costs_one_attempt(self) -> None:
        """A pass leaves the submission in place, so the same rule holds."""
        state = self._state()
        storage = _Storage(state)
        tool = self._tool(storage, state)

        await tool(passed=True)
        await tool(passed=True)

        self.assertEqual(len(storage.state.steps[0].verifications), 1)
        self.assertIs(storage.state.steps[0].phase, SOPPhase.COMPLETED)

    async def test_new_work_is_charged_as_a_new_attempt(self) -> None:
        """A fresh submission is a fresh attempt, and a fresh verdict."""
        state = self._state()
        storage = _Storage(state)
        tool = self._tool(storage, state)

        await tool(passed=False, message="no")

        storage.state.steps[0].submission = [TextBlock(text="did A again")]
        storage.state.steps[0].phase = SOPPhase.RUNNING

        await tool(passed=False, message="still no")

        self.assertEqual(len(storage.state.steps[0].verifications), 2)
        self.assertEqual(
            [_.message for _ in storage.state.steps[0].verifications],
            ["no", "still no"],
        )
