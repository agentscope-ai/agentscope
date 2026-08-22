# -*- coding: utf-8 -*-
"""Tests for the SOP decision functions.

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
    SOPRunStatus,
    SOPStep,
    SOPStepState,
    VerificationRecord,
    new_run,
    next_actions,
)
from agentscope.sop import _engine as core
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


def _passed(by: str = "judge") -> VerificationRecord:
    """An accepting verdict."""
    return VerificationRecord(passed=True, verified_by=by)


def _failed(why: str) -> VerificationRecord:
    """A refusing verdict."""
    return VerificationRecord(passed=False, message=why, verified_by="judge")


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

        self.assertEqual(SOPStepState.PENDING, run.steps["a"].state)
        self.assertEqual(1, len(run.steps["a"].verifications))
        self.assertEqual("no acceptance criteria", core.feedback(run, "a"))
        # Back in the queue, so the next pass asks it again.
        self.assertEqual([Dispatch("a")], next_actions(sop, run))

    def test_running_out_of_attempts_fails_the_step(self) -> None:
        """The attempt limit is a hard stop, not a suggestion."""
        sop = _linear_sop(max_attempts=2)
        run = new_run(sop)
        core.mark_dispatched(run, "a")
        core.mark_submitted(run, "a", "nope")
        core.record_verification(sop, run, "a", _failed("first"))
        core.record_verification(sop, run, "a", _failed("second"))

        self.assertEqual(SOPStepState.FAILED, run.steps["a"].state)
        self.assertEqual(2, len(run.steps["a"].verifications))

    def test_a_failure_strands_everything_behind_it(self) -> None:
        """A stranded step needs no state of its own — it never ran."""
        sop = _linear_sop(max_attempts=1)
        run = new_run(sop)
        core.mark_dispatched(run, "a")
        core.mark_submitted(run, "a", "nope")
        core.record_verification(sop, run, "a", _failed("not close"))

        self.assertEqual(SOPStepState.PENDING, run.steps["b"].state)
        self.assertEqual(SOPStepState.PENDING, run.steps["c"].state)
        self.assertEqual(
            [Settle(SOPRunStatus.FAILED, "no step can proceed")],
            next_actions(sop, run),
        )

    def test_a_finished_run_settles_completed(self) -> None:
        """Nothing left and nothing failed means the run is done."""
        sop = _linear_sop()
        run = new_run(sop)
        for step_id in ("a", "b", "c"):
            _complete(sop, run, step_id)

        self.assertEqual(
            [Settle(SOPRunStatus.COMPLETED)],
            next_actions(sop, run),
        )

    def test_no_answer_yet_leaves_the_step_being_verified(self) -> None:
        """A verifier with nothing to say costs the step nothing.

        It stays in VERIFYING and is simply asked again — which is how a
        human verifier avoids being a special case in the engine.
        """
        sop = _linear_sop()
        run = new_run(sop)
        core.mark_dispatched(run, "a")
        core.mark_submitted(run, "a", "please review")

        self.assertEqual(SOPStepState.VERIFYING, run.steps["a"].state)
        self.assertEqual([], run.steps["a"].verifications)
        self.assertEqual([Judge("a")], next_actions(sop, run))

        core.record_verification(sop, run, "a", _passed("lead"))
        self.assertEqual(SOPStepState.COMPLETED, run.steps["a"].state)

    def test_a_dependency_cycle_is_reported_not_hung_on(self) -> None:
        """Two steps blocking each other settles as failed, not silence."""
        sop = SOP(name="cycle", steps=[_step("a", ["b"]), _step("b", ["a"])])
        run = new_run(sop)

        actions = next_actions(sop, run)
        self.assertEqual(1, len(actions))
        self.assertIsInstance(actions[0], Settle)
        self.assertEqual(SOPRunStatus.FAILED, actions[0].status)
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

        def decide(sop, run, step, step_run) -> VerificationRecord:
            seen["sop"] = sop.name
            seen["steps"] = len(run.steps)
            seen["subject"] = step.subject
            seen["agent"] = step.agent.name
            seen["submission"] = step_run.submission
            return VerificationRecord(passed=True)

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

        self.assertTrue(result.passed)
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
