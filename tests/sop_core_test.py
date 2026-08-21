# -*- coding: utf-8 -*-
"""Tests for the SOP decision core.

Every case here drives the state machine by hand — no models, no I/O —
which is exactly what putting the decisions in pure functions buys.
"""
import asyncio
from unittest import TestCase

from agentscope.agent import Agent
from agentscope.sop import (
    SOP,
    CallbackVerifier,
    Dispatch,
    Judge,
    Settle,
    SOPStep,
    VerifyResult,
    core,
    new_run,
    next_actions,
)
from agentscope.state import Task

from tests.utils import MockModel


def _agent(name: str = "worker") -> Agent:
    """A stand-in agent — the core never calls it, it only carries it."""
    return Agent(name=name, system_prompt="", model=MockModel())


def _step(step_id: str, blocked_by: list | None = None, **kw) -> SOPStep:
    """A step with an agent already attached."""
    return SOPStep(
        id=step_id,
        subject=step_id.upper(),
        agent=_agent(),
        blocked_by=blocked_by or [],
        **kw,
    )


def _linear_sop(max_attempts: int = 3) -> SOP:
    """A → B → C."""
    return SOP(
        name="linear",
        steps=[
            _step("a", max_attempts=max_attempts),
            _step("b", ["a"], max_attempts=max_attempts),
            _step("c", ["b"], max_attempts=max_attempts),
        ],
    )


def _passed(by: str = "judge") -> VerifyResult:
    """An accepting verdict."""
    return VerifyResult(status="passed", verified_by=by)


def _failed(why: str) -> VerifyResult:
    """A refusing verdict."""
    return VerifyResult(status="failed", message=why, verified_by="judge")


def _complete(sop: SOP, run: object, step_id: str, text: str = "ok") -> None:
    """Walk a step all the way through to completed."""
    core.mark_dispatched(run, step_id)
    core.mark_submitted(run, step_id, text)
    core.record_verification(sop, run, step_id, _passed())


class SOPCoreTest(TestCase):
    """Readiness, retries, failure spreading and settling."""

    def test_only_unblocked_steps_dispatch(self) -> None:
        """A fresh run offers its entry steps and nothing behind them."""
        sop = _linear_sop()
        run = new_run(sop)

        self.assertEqual([Dispatch("a")], next_actions(sop, run))

    def test_diamond_fans_out_and_joins(self) -> None:
        """Both middle steps go at once; the join waits for both."""
        sop = SOP(
            name="diamond",
            steps=[
                _step("a"),
                _step("b", ["a"]),
                _step("c", ["a"]),
                _step("d", ["b", "c"]),
            ],
        )
        run = new_run(sop)
        _complete(sop, run, "a")

        self.assertEqual(
            [Dispatch("b"), Dispatch("c")],
            next_actions(sop, run),
        )

        _complete(sop, run, "b")
        self.assertEqual([Dispatch("c")], next_actions(sop, run))

        _complete(sop, run, "c")
        self.assertEqual([Dispatch("d")], next_actions(sop, run))

    def test_submission_asks_to_be_judged(self) -> None:
        """A step that has submitted wants judging, not dispatching."""
        sop = _linear_sop()
        run = new_run(sop)
        core.mark_dispatched(run, "a")
        core.mark_submitted(run, "a", "here is the plan")

        self.assertEqual([Judge("a")], next_actions(sop, run))
        self.assertEqual("here is the plan", run.steps["a"].submission)

    def test_refusal_sends_the_step_back_with_a_reason(self) -> None:
        """Below the attempt limit, a refused step keeps working."""
        sop = _linear_sop()
        run = new_run(sop)
        core.mark_dispatched(run, "a")
        core.mark_submitted(run, "a", "half a plan")
        core.record_verification(
            sop,
            run,
            "a",
            _failed("no acceptance criteria"),
        )

        self.assertEqual("running", run.steps["a"].state)
        self.assertEqual(1, len(run.steps["a"].verifications))
        self.assertEqual("no acceptance criteria", core.feedback(run, "a"))

    def test_running_out_of_attempts_fails_the_step(self) -> None:
        """The attempt limit is a hard stop, not a suggestion."""
        sop = _linear_sop(max_attempts=2)
        run = new_run(sop)
        core.mark_dispatched(run, "a")
        core.mark_submitted(run, "a", "nope")
        core.record_verification(sop, run, "a", _failed("first"))
        core.record_verification(sop, run, "a", _failed("second"))

        self.assertEqual("failed", run.steps["a"].state)
        self.assertEqual(2, len(run.steps["a"].verifications))

    def test_a_failure_strands_everything_behind_it(self) -> None:
        """Skipping cascades — B is stranded, and so is C behind it."""
        sop = _linear_sop(max_attempts=1)
        run = new_run(sop)
        core.mark_dispatched(run, "a")
        core.mark_submitted(run, "a", "nope")
        core.record_verification(sop, run, "a", _failed("not close"))

        self.assertEqual("skipped", run.steps["b"].state)
        self.assertEqual("skipped", run.steps["c"].state)
        self.assertEqual([Settle("failed")], next_actions(sop, run))

    def test_a_finished_run_settles_completed(self) -> None:
        """Nothing left and nothing failed means the run is done."""
        sop = _linear_sop()
        run = new_run(sop)
        for step_id in ("a", "b", "c"):
            _complete(sop, run, step_id)

        self.assertEqual([Settle("completed")], next_actions(sop, run))

    def test_pending_parks_the_step_and_spends_no_attempt(self) -> None:
        """Waiting on an answer is not a refusal, so it costs nothing.

        The step is simply judged again on the next pass — which is how a
        human verifier avoids being a special case in the engine.
        """
        sop = _linear_sop()
        run = new_run(sop)
        core.mark_dispatched(run, "a")
        core.mark_submitted(run, "a", "please review")
        core.record_verification(
            sop,
            run,
            "a",
            VerifyResult(status="pending"),
        )

        self.assertEqual("awaiting_approval", run.steps["a"].state)
        self.assertEqual([], run.steps["a"].verifications)
        self.assertEqual([Judge("a")], next_actions(sop, run))

        core.record_verification(sop, run, "a", _passed("lead"))
        self.assertEqual("completed", run.steps["a"].state)

    def test_a_dependency_cycle_is_reported_not_hung_on(self) -> None:
        """Two steps blocking each other settles as failed, not silence."""
        sop = SOP(name="cycle", steps=[_step("a", ["b"]), _step("b", ["a"])])
        run = new_run(sop)

        actions = next_actions(sop, run)
        self.assertEqual(1, len(actions))
        self.assertIsInstance(actions[0], Settle)
        self.assertEqual("failed", actions[0].state)
        self.assertTrue(actions[0].reason)

    def test_a_step_reads_what_its_blockers_submitted(self) -> None:
        """The handover is text, keyed by the step that produced it."""
        sop = _linear_sop()
        run = new_run(sop)
        _complete(sop, run, "a", text="the plan")

        self.assertEqual(
            {"A": "the plan"},
            core.upstream_submissions(sop, run, "b"),
        )

    def test_a_verifier_sees_the_whole_picture(self) -> None:
        """verify() gets definition and runtime side by side, and can
        reach the live agent through the step it is judging."""
        seen = {}

        def decide(sop, run, step, step_run) -> VerifyResult:
            seen["sop"] = sop.name
            seen["steps"] = len(run.steps)
            seen["subject"] = step.subject
            seen["agent"] = step.agent.name
            seen["submission"] = step_run.submission
            return VerifyResult(status="passed")

        sop = SOP(
            name="one",
            steps=[_step("a", verifier=CallbackVerifier(decide))],
        )
        run = new_run(sop)
        core.mark_dispatched(run, "a")
        core.mark_submitted(run, "a", "done")

        step, record = sop.steps[0], run.steps["a"]
        result = asyncio.run(
            step.verifier.verify(sop, run, step, record),
        )

        self.assertEqual("passed", result.status)
        self.assertEqual("callback", result.verified_by)
        self.assertEqual(
            {
                "sop": "one",
                "steps": 1,
                "subject": "A",
                "agent": "worker",
                "submission": "done",
            },
            seen,
        )

    def test_a_step_may_be_seeded_with_planning_tasks(self) -> None:
        """Preset tasks narrow the agent's freedom without removing it."""
        step = _step(
            "a",
            tasks=[
                Task(subject="Read the spec", description="", metadata={}),
                Task(subject="Sketch the API", description="", metadata={}),
            ],
        )

        self.assertEqual(2, len(step.tasks))
        self.assertEqual("Read the spec", step.tasks[0].subject)
        self.assertEqual([], _step("b").tasks)
