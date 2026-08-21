# -*- coding: utf-8 -*-
"""What a run should do next — decided without touching anything.

Every function here reads a :class:`~._model.SOP` and a
:class:`~._run.SOPRun` and either answers a question or moves the run's
state forward. Nothing calls a model, spawns an agent, writes a file, or
awaits. That is the point: this is the part of a SOP engine that is easy
to get subtly wrong — readiness, retry counting, failure spreading to
everything downstream, telling "stuck" apart from "finished" — so it is
written once and shared.

Two drivers put it to work, and they differ in shape, not just in I/O:

- The **in-process driver** loops: ask :func:`next_actions`, carry them
  out, ask again, until the run settles.
- The **service driver** cannot loop. A run may sit for hours waiting on a
  person, so it asks :func:`next_actions` once per trigger, carries those
  out, and parks.

Both get the same answers, because both ask the same function.
"""
from dataclasses import dataclass
from typing import Union

from ._model import SOP, SOPStep
from ._run import RunState, SOPRun, StepRun, VerificationRecord
from .._utils._common import _generate_timestamp

# ══════════════════════════════════════════════════════════════════════
# Actions — what a driver is being asked to carry out
# ══════════════════════════════════════════════════════════════════════


@dataclass
class Dispatch:
    """Hand a step to its executor and collect what it submits."""

    step_id: str
    """The step to run."""


@dataclass
class Judge:
    """Judge a submitted step against its acceptance criteria."""

    step_id: str
    """The step to judge."""


@dataclass
class AskApproval:
    """Put a step in front of a person for the first time."""

    step_id: str
    """The step awaiting sign-off."""


@dataclass
class PollApproval:
    """See whether a person has answered yet."""

    step_id: str
    """The step still waiting."""


@dataclass
class Settle:
    """Nothing is left to do; the run has reached ``state``."""

    state: RunState
    """How the run ended."""

    reason: str = ""
    """Why, when it did not simply finish."""


Action = Union[Dispatch, Judge, AskApproval, PollApproval, Settle]
"""One thing a driver should do. See :func:`next_actions`."""


# ══════════════════════════════════════════════════════════════════════
# Questions
# ══════════════════════════════════════════════════════════════════════


def is_ready(sop: SOP, run: SOPRun, step_id: str) -> bool:
    """Whether a pending step's blockers have all completed.

    Readiness is recomputed rather than stored, so there is never a stale
    "ready" flag to disagree with the states it was derived from.

    Args:
        sop (`SOP`):
            The definition being run.
        run (`SOPRun`):
            The run in progress.
        step_id (`str`):
            The step to test.

    Returns:
        `bool`:
            ``True`` when every blocker has completed.
    """
    step = _step(sop, step_id)
    if step is None:
        return False
    return all(
        (r := run.step(b)) is not None and r.state == "completed"
        for b in step.blocked_by
    )


def overall_state(run: SOPRun) -> RunState:
    """Work out where the run stands as a whole.

    Args:
        run (`SOPRun`):
            The run in progress.

    Returns:
        `RunState`:
            ``completed`` when every step settled and none failed,
            ``failed`` when any did, otherwise ``running``.
    """
    unsettled = ("pending", "running", "verifying", "awaiting_approval")
    if any(s.state in unsettled for s in run.steps):
        return "running"
    if any(s.state == "failed" for s in run.steps):
        return "failed"
    return "completed"


def upstream_submissions(
    sop: SOP,
    run: SOPRun,
    step_id: str,
) -> dict[str, str]:
    """Collect what a step's blockers submitted, keyed by their title.

    This is the whole handover between steps: text, nothing else.

    Args:
        sop (`SOP`):
            The definition being run.
        run (`SOPRun`):
            The run in progress.
        step_id (`str`):
            The step about to be dispatched.

    Returns:
        `dict[str, str]`:
            Blocker title → the text it submitted.
    """
    step = _step(sop, step_id)
    if step is None:
        return {}
    out: dict[str, str] = {}
    for blocker_id in step.blocked_by:
        blocker, record = _step(sop, blocker_id), run.step(blocker_id)
        if blocker is not None and record is not None:
            out[blocker.title] = record.submission
    return out


def next_actions(sop: SOP, run: SOPRun) -> list[Action]:
    """Decide everything that can be done right now.

    The returned actions are independent, so a driver may carry them out
    concurrently. An empty-handed run yields a single :class:`Settle`.

    Args:
        sop (`SOP`):
            The definition being run.
        run (`SOPRun`):
            The run in progress.

    Returns:
        `list[Action]`:
            What to do, or one :class:`Settle` when the run is over.
    """
    actions: list[Action] = []
    for record in run.steps:
        if record.state == "pending" and is_ready(sop, run, record.step_id):
            actions.append(Dispatch(record.step_id))
        elif record.state == "verifying":
            actions.append(Judge(record.step_id))
        elif record.state == "awaiting_approval":
            asked = any(
                v.verified_by == _PENDING for v in record.verifications
            )
            actions.append(
                PollApproval(record.step_id)
                if asked
                else AskApproval(record.step_id),
            )

    if actions:
        return actions

    state = overall_state(run)
    if state == "running":
        # Nothing runnable and nothing settled: a blocker cycle, or a
        # blocked_by pointing at a step that is not in this run.
        return [Settle("failed", "no step can proceed")]
    return [Settle(state)]


# ══════════════════════════════════════════════════════════════════════
# Transitions
# ══════════════════════════════════════════════════════════════════════

_PENDING = "__awaiting__"
"""Marker on the record written when a person has been asked but has not
answered. Lets :func:`next_actions` tell "ask" from "poll" without a
second field."""


def mark_dispatched(run: SOPRun, step_id: str) -> None:
    """Note that a step has been handed to its executor.

    Args:
        run (`SOPRun`):
            The run in progress.
        step_id (`str`):
            The step being dispatched.
    """
    record = run.step(step_id)
    if record is None:
        return
    record.state = "running"
    record.started_at = record.started_at or _generate_timestamp()


def mark_submitted(run: SOPRun, step_id: str, submission: str) -> None:
    """Record what a step's agent handed back, ready to be judged.

    Args:
        run (`SOPRun`):
            The run in progress.
        step_id (`str`):
            The step that finished working.
        submission (`str`):
            The text it submitted.
    """
    record = run.step(step_id)
    if record is None:
        return
    record.submission = submission
    record.state = "verifying"


def mark_awaiting_approval(run: SOPRun, step_id: str) -> None:
    """Note that a person has been asked and has not answered yet.

    Args:
        run (`SOPRun`):
            The run in progress.
        step_id (`str`):
            The step now waiting on a person.
    """
    record = run.step(step_id)
    if record is None:
        return
    record.state = "awaiting_approval"
    record.verifications.append(
        VerificationRecord(
            attempt=record.attempts + 1,
            passed=False,
            message="waiting for a person",
            verified_by=_PENDING,
        ),
    )


def record_verification(
    sop: SOP,
    run: SOPRun,
    step_id: str,
    passed: bool,
    message: str = "",
    verified_by: str = "",
) -> None:
    """Settle one acceptance attempt and move the step accordingly.

    Passing completes the step. Failing sends it back to its agent with
    the reason — unless it has now failed
    :attr:`~._model.SOPStep.max_attempts` times, in which case the step
    fails and everything downstream of it is skipped.

    Args:
        sop (`SOP`):
            The definition being run.
        run (`SOPRun`):
            The run in progress.
        step_id (`str`):
            The step that was judged.
        passed (`bool`):
            Whether it was accepted.
        message (`str`, optional):
            Why it was refused. This goes back to the agent verbatim, so
            it should say what is missing.
        verified_by (`str`, optional):
            The judging model, or the approver.
    """
    record, step = run.step(step_id), _step(sop, step_id)
    if record is None or step is None:
        return

    # Drop the "waiting" placeholder, if a person was asked.
    record.verifications = [
        v for v in record.verifications if v.verified_by != _PENDING
    ]
    record.attempts += 1
    record.verifications.append(
        VerificationRecord(
            attempt=record.attempts,
            passed=passed,
            message=message,
            verified_by=verified_by,
        ),
    )

    if passed:
        record.state = "completed"
        record.finished_at = _generate_timestamp()
    elif record.attempts >= step.max_attempts:
        record.state = "failed"
        record.finished_at = _generate_timestamp()
        skip_unreachable(sop, run)
    else:
        # Back to the agent, with the reason waiting in `feedback`.
        record.state = "running"


def feedback(run: SOPRun, step_id: str) -> str:
    """The reason the last attempt was refused, for the retry prompt.

    Args:
        run (`SOPRun`):
            The run in progress.
        step_id (`str`):
            The step being retried.

    Returns:
        `str`:
            The refusal message, or ``""`` on a first attempt.
    """
    record = run.step(step_id)
    if record is None or not record.verifications:
        return ""
    last = record.verifications[-1]
    return "" if last.passed else last.message


def skip_unreachable(sop: SOP, run: SOPRun) -> None:
    """Mark as skipped every pending step that can no longer be reached.

    A step is unreachable once any blocker has failed or been skipped.
    Applied repeatedly, since skipping one step can strand the next.

    Args:
        sop (`SOP`):
            The definition being run.
        run (`SOPRun`):
            The run in progress.
    """
    changed = True
    while changed:
        changed = False
        for record in run.steps:
            if record.state != "pending":
                continue
            step = _step(sop, record.step_id)
            if step is None:
                continue
            blocked = any(
                (r := run.step(b)) is not None
                and r.state in ("failed", "skipped")
                for b in step.blocked_by
            )
            if blocked:
                record.state = "skipped"
                record.finished_at = _generate_timestamp()
                changed = True


def settle(run: SOPRun, state: RunState) -> None:
    """Close the run out.

    Args:
        run (`SOPRun`):
            The run to close.
        state (`RunState`):
            How it ended.
    """
    run.state = state
    run.finished_at = _generate_timestamp()


def new_run(sop: SOP, inputs: dict[str, str] | None = None) -> SOPRun:
    """Start a run of ``sop``, with one pending record per step.

    Args:
        sop (`SOP`):
            The definition to run.
        inputs (`dict[str, str] | None`, optional):
            Values for the SOP's declared inputs.

    Returns:
        `SOPRun`:
            A fresh run, not yet advanced.
    """
    return SOPRun(
        sop_id=sop.id,
        inputs=dict(inputs or {}),
        steps=[StepRun(step_id=s.id) for s in sop.steps],
    )


def _step(sop: SOP, step_id: str) -> SOPStep | None:
    """Look a step up in the definition."""
    return next((s for s in sop.steps if s.id == step_id), None)
