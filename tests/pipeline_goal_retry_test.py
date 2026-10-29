# -*- coding: utf-8 -*-
"""Regression tests: GoalPipeline retry budget must not leak across
goal-loop iterations.

The retry counters were made instance state by #3078 so that a HITL
resume keeps its budget. They are only reset on a *fresh run*, however,
so retries also accumulate across the pipeline's own goal-loop
iterations: an executor that needs one nudge in iteration 1 and one nudge
in iteration 2 has consumed "2 retries" even though neither report
generation ever retried more than once. With ``max_retries=1`` the
pipeline then aborts mid-goal with a message claiming the executor
"failed to generate a valid execution report after 1 retries" — even
though it generated valid reports in between.
"""

from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.message import UserMsg
from agentscope.pipeline import GoalPipeline

from pipeline_goal_test import (
    StubAgent,
    _no_output,
    _report,
    _verdict,
)


class GoalPipelineRetryBudgetAcrossIterationsTest(IsolatedAsyncioTestCase):
    """Each report/verdict generation gets its own retry budget."""

    async def _run(self, pipe: GoalPipeline, inputs) -> list:
        """Drain one pipeline run into what it yielded."""
        return [chunk async for chunk in pipe.reply_stream(inputs)]

    def _reports(self, yielded: list) -> list:
        """The executor reports that flowed out, in order."""
        return [
            chunk.structured_output.get("report")
            for chunk in yielded
            if getattr(chunk, "structured_output", None)
            and "report" in chunk.structured_output
        ]

    async def test_executor_retry_budget_does_not_leak_across_iterations(
        self,
    ) -> None:
        """One nudge per iteration is within a ``max_retries=1`` budget.

        Iteration 1: the executor needs one retry, then reports; the
        verifier says "fail". Iteration 2: the executor needs one retry
        again, then reports; the verifier says "pass". Each *report
        generation* only ever retried once, so the run must complete
        instead of aborting with the misleading "after 1 retries" error.
        """
        executor = StubAgent(
            "executor",
            [
                [_no_output("executor")],  # iter 1, retry 1 (allowed)
                [_report("v1")],  # iter 1, success
                [_no_output("executor")],  # iter 2, retry 1 (allowed)
                [_report("v2")],  # iter 2, success
            ],
        )
        verifier = StubAgent(
            "verifier",
            [
                [_verdict("fail", "fix it")],  # iter 1 verdict
                [_verdict("pass")],  # iter 2 verdict
            ],
        )
        pipe = GoalPipeline(executor, verifier, max_retries=1)

        yielded = await self._run(
            pipe, UserMsg(name="user", content="goal")
        )

        # The run reached the second iteration's report and the passing
        # verdict ended it — no RuntimeError aborted the goal midway.
        self.assertEqual(self._reports(yielded), ["v1", "v2"])
        self.assertEqual(len(executor.received), 4)
        self.assertEqual(len(verifier.received), 2)

    async def test_verifier_retry_budget_does_not_leak_across_iterations(
        self,
    ) -> None:
        """One missing verdict per iteration is within budget too."""
        executor = StubAgent(
            "executor",
            [
                [_report("v1")],  # iter 1 report
                [_report("v2")],  # iter 2 report
            ],
        )
        verifier = StubAgent(
            "verifier",
            [
                [],  # iter 1, retry 1 (allowed)
                [_verdict("fail", "x")],  # iter 1 verdict
                [],  # iter 2, retry 1 (allowed)
                [_verdict("pass")],  # iter 2 verdict
            ],
        )
        pipe = GoalPipeline(executor, verifier, max_retries=1)

        yielded = await self._run(
            pipe, UserMsg(name="user", content="goal")
        )

        self.assertEqual(self._reports(yielded), ["v1", "v2"])
        self.assertEqual(len(executor.received), 2)
        self.assertEqual(len(verifier.received), 4)
