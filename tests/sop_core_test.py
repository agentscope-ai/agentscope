# -*- coding: utf-8 -*-
"""Tests for the SOP decision core.

Every case here drives the state machine by hand — no agents, no models,
no I/O — which is exactly what putting the decisions in pure functions
buys.
"""
from unittest import TestCase

from agentscope.agent import Agent
from agentscope.sop import (
    SOP,
    Acceptance,
    AskApproval,
    Dispatch,
    Judge,
    PollApproval,
    Settle,
    SOPStep,
    core,
    new_run,
    next_actions,
)
from tests.utils import MockModel


def _agent(name: str = "worker") -> Agent:
    """A stand-in agent — the core never calls it, it only carries it."""
    return Agent(name=name, system_prompt="", model=MockModel())


def _linear_sop(max_attempts: int = 3) -> SOP:
    """A → B → C, each accepted by an LLM."""
    return SOP(
        name="linear",
        steps=[
            SOPStep(
                id="a",
                title="A",
                agent=_agent(),
                max_attempts=max_attempts,
            ),
            SOPStep(
                id="b",
                title="B",
                agent=_agent(),
                blocked_by=["a"],
                max_attempts=max_attempts,
            ),
            SOPStep(
                id="c",
                title="C",
                agent=_agent(),
                blocked_by=["b"],
                max_attempts=max_attempts,
            ),
        ],
    )


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
                SOPStep(id="a", title="A", agent=_agent()),
                SOPStep(id="b", title="B", agent=_agent(), blocked_by=["a"]),
                SOPStep(id="c", title="C", agent=_agent(), blocked_by=["a"]),
                SOPStep(
                    id="d",
                    title="D",
                    agent=_agent(),
                    blocked_by=["b", "c"],
                ),
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
        self.assertEqual("here is the plan", run.step("a").submission)

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
            False,
            "no acceptance criteria",
        )

        self.assertEqual("running", run.step("a").state)
        self.assertEqual(1, run.step("a").attempts)
        self.assertEqual("no acceptance criteria", core.feedback(run, "a"))

    def test_running_out_of_attempts_fails_the_step(self) -> None:
        """The attempt limit is a hard stop, not a suggestion."""
        sop = _linear_sop(max_attempts=2)
        run = new_run(sop)
        core.mark_dispatched(run, "a")
        core.mark_submitted(run, "a", "nope")
        core.record_verification(sop, run, "a", False, "first")
        core.record_verification(sop, run, "a", False, "second")

        self.assertEqual("failed", run.step("a").state)
        self.assertIsNotNone(run.step("a").finished_at)

    def test_a_failure_strands_everything_behind_it(self) -> None:
        """Skipping cascades — B is stranded, and so is C behind it."""
        sop = _linear_sop(max_attempts=1)
        run = new_run(sop)
        core.mark_dispatched(run, "a")
        core.mark_submitted(run, "a", "nope")
        core.record_verification(sop, run, "a", False, "not close")

        self.assertEqual("skipped", run.step("b").state)
        self.assertEqual("skipped", run.step("c").state)
        self.assertEqual([Settle("failed")], next_actions(sop, run))

    def test_a_finished_run_settles_completed(self) -> None:
        """Nothing left and nothing failed means the run is done."""
        sop = _linear_sop()
        run = new_run(sop)
        for step_id in ("a", "b", "c"):
            _complete(sop, run, step_id)

        self.assertEqual([Settle("completed")], next_actions(sop, run))

    def test_human_acceptance_is_asked_once_then_polled(self) -> None:
        """The first pass asks a person; later passes only check back."""
        sop = SOP(
            name="review",
            steps=[
                SOPStep(
                    id="a",
                    title="A",
                    agent=_agent(),
                    acceptance=Acceptance(kind="human", approver="lead"),
                ),
            ],
        )
        run = new_run(sop)
        core.mark_dispatched(run, "a")
        core.mark_submitted(run, "a", "please review")
        run.step("a").state = "awaiting_approval"

        self.assertEqual([AskApproval("a")], next_actions(sop, run))

        core.mark_awaiting_approval(run, "a")
        self.assertEqual([PollApproval("a")], next_actions(sop, run))

        core.record_verification(sop, run, "a", True, verified_by="lead")
        self.assertEqual("completed", run.step("a").state)
        self.assertEqual([Settle("completed")], next_actions(sop, run))

    def test_a_dependency_cycle_is_reported_not_hung_on(self) -> None:
        """Two steps blocking each other settles as failed, not silence."""
        sop = SOP(
            name="cycle",
            steps=[
                SOPStep(id="a", title="A", agent=_agent(), blocked_by=["b"]),
                SOPStep(id="b", title="B", agent=_agent(), blocked_by=["a"]),
            ],
        )
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
        _complete(sop, run, "a", submission="the plan")

        self.assertEqual(
            {"A": "the plan"},
            core.upstream_submissions(sop, run, "b"),
        )


def _complete(
    sop: SOP,
    run: object,
    step_id: str,
    submission: str = "ok",
) -> None:
    """Walk a step all the way through to completed."""
    core.mark_dispatched(run, step_id)
    core.mark_submitted(run, step_id, submission)
    core.record_verification(sop, run, step_id, True, verified_by="judge")
