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

Note there is no separate action for human sign-off. A verifier that needs
a person returns ``pending``; the step parks and is simply judged again on
a later pass. Waiting on a person and waiting on a model take one path.

Both get the same answers, because both ask the same function.
"""
from dataclasses import dataclass
from typing import Union

from ._model import SOP, SOPStep
from ._run import RunState, SOPRun, StepRun, VerificationRecord
from ._verifier import VerifyResult
from ..message import DataBlock, TextBlock

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
    """Ask a step's verifier what it makes of the submission.

    Also issued for a step already waiting on an outside answer: a
    verifier that returns ``pending`` is simply asked again, so waiting on
    a person and waiting on a model are the same path through the engine.
    """

    step_id: str
    """The step to judge."""


@dataclass
class Settle:
    """Nothing is left to do; the run has reached ``state``."""

    state: RunState
    """How the run ended."""

    reason: str = ""
    """Why, when it did not simply finish."""


Action = Union[Dispatch, Judge, Settle]
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
        (r := run.steps.get(b)) is not None and r.state == "completed"
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
    records = run.steps.values()
    if any(r.state in unsettled for r in records):
        return "running"
    if any(r.state == "failed" for r in records):
        return "failed"
    return "completed"


def upstream_submissions(
    sop: SOP,
    run: SOPRun,
    step_id: str,
) -> dict[str, str]:
    """Collect what a step's blockers submitted, keyed by their subject.

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
            Blocker subject → the text it submitted.
    """
    step = _step(sop, step_id)
    if step is None:
        return {}
    out: dict[str, str] = {}
    for blocker_id in step.blocked_by:
        blocker = _step(sop, blocker_id)
        record = run.steps.get(blocker_id)
        if blocker is not None and record is not None:
            out[blocker.subject] = record.submission
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
    for step in sop.steps:
        record = run.steps.get(step.id)
        if record is None:
            continue
        if record.state == "pending" and is_ready(sop, run, step.id):
            actions.append(Dispatch(step.id))
        elif record.state in ("verifying", "awaiting_approval"):
            actions.append(Judge(step.id))

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

def mark_dispatched(run: SOPRun, step_id: str) -> None:
    """Note that a step has been handed to its executor.

    Args:
        run (`SOPRun`):
            The run in progress.
        step_id (`str`):
            The step being dispatched.
    """
    record = run.steps.get(step_id)
    if record is None:
        return
    record.state = "running"


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
    record = run.steps.get(step_id)
    if record is None:
        return
    record.submission = submission
    record.state = "verifying"


def record_verification(
    sop: SOP,
    run: SOPRun,
    step_id: str,
    result: VerifyResult,
) -> None:
    """Apply a verifier's verdict and move the step accordingly.

    ``pending`` parks the step and writes nothing — no attempt is spent,
    because nothing was judged. ``passed`` completes it. ``failed`` sends
    it back to its agent with the reason, unless that was its
    :attr:`~._model.SOPStep.max_attempts` refusal, in which case the step
    fails and everything downstream of it is skipped.

    The attempt count is not tracked separately: it is how many verdicts
    have been recorded.

    Args:
        sop (`SOP`):
            The definition being run.
        run (`SOPRun`):
            The run in progress.
        step_id (`str`):
            The step that was judged.
        result (`VerifyResult`):
            What its verifier concluded.
    """
    record, step = run.steps.get(step_id), _step(sop, step_id)
    if record is None or step is None:
        return

    if result.status == "pending":
        record.state = "awaiting_approval"
        return

    record.verifications.append(
        VerificationRecord(
            passed=result.status == "passed",
            message=result.message,
            verified_by=result.verified_by,
        ),
    )

    if result.status == "passed":
        record.state = "completed"
    elif len(record.verifications) >= step.max_attempts:
        record.state = "failed"
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
    record = run.steps.get(step_id)
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
        for step in sop.steps:
            record = run.steps.get(step.id)
            if record is None or record.state != "pending":
                continue
            blocked = any(
                (r := run.steps.get(b)) is not None
                and r.state in ("failed", "skipped")
                for b in step.blocked_by
            )
            if blocked:
                record.state = "skipped"
                changed = True


def new_run(
    sop: SOP,
    inputs: list[TextBlock | DataBlock] | None = None,
    /,
) -> SOPRun:
    """Start a run of ``sop``, with one pending record per step.

    Args:
        sop (`SOP`):
            The definition to run.
        inputs (`list[TextBlock | DataBlock] | None`, optional):
            What the run is started with. Content rather than named
            values, so it carries images and files as readily as text.

    Returns:
        `SOPRun`:
            A fresh run, not yet advanced.
    """
    return SOPRun(
        sop_id=sop.id,
        inputs=list(inputs or []),
        steps={s.id: StepRun() for s in sop.steps},
    )


def _step(sop: SOP, step_id: str) -> SOPStep | None:
    """Look a step up in the definition."""
    return next((s for s in sop.steps if s.id == step_id), None)
