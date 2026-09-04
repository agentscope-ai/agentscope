# -*- coding: utf-8 -*-
"""What a run of a SOP did.

One :class:`~._sop.SOP` definition produces many runs. A definition holds
live agents and verifiers and cannot be written down; everything here is
plain data, and this is the half worth persisting — dump a run and the
progress outlives the process that made it.

Nothing in here is a cursor. There is no "current step" and no "next
index": a pass over a run recomputes what can proceed from the steps' own
states, so picking a half-finished run back up is the same operation as
starting one.

The three fields a step keeps are the three that cannot be worked out
again. Its ``state`` says whether to run it or skip it. Its ``submission``
is what the next steps read — and because a completed step is *skipped*
rather than re-run, losing it would leave them with nothing. Its
``verifications`` are the verdicts so far, which say both why it was sent
back and, by their number, how close it is to being given up on.
"""
from enum import StrEnum

from pydantic import BaseModel, Field

from ..message import DataBlock, TextBlock
from .._utils._common import _generate_id, _generate_timestamp


class SOPStepState(StrEnum):
    """Where a step stands in a run.

    A step that never ran stays ``PENDING`` — including one stranded
    behind a failure, which needs no state of its own to say so.

    Waiting is not a state either. An agent stopped on a permission
    prompt is still ``RUNNING``, and a verifier that has not answered yet
    leaves the step ``VERIFYING``, whether the answer is a second away or
    a person is away for the weekend.
    """

    PENDING = "pending"
    """Not dispatched — still blocked, next up, sent back to try again,
    or never reached at all."""

    RUNNING = "running"
    """Handed to its agent."""

    VERIFYING = "verifying"
    """The agent submitted; its verifier has not settled yet."""

    COMPLETED = "completed"
    """Accepted."""

    FAILED = "failed"
    """Refused until :attr:`~._sop.SOPStep.max_attempts` ran out."""


class SOPRunStatus(StrEnum):
    """Where a run stands overall.

    Always derived from the steps — see
    :func:`~._engine.overall_status` — never stored.
    """

    RUNNING = "running"
    """Something can still proceed."""

    COMPLETED = "completed"
    """Every step was accepted."""

    FAILED = "failed"
    """A step failed, or nothing left can ever proceed."""


class VerificationRecord(BaseModel):
    """One settled verdict on a step.

    Only settled verdicts exist. A verifier with no answer yet returns
    nothing at all rather than a record saying so, because a verdict that
    has not happened is not a verdict.
    """

    passed: bool
    """Whether the step was accepted."""

    message: str = ""
    """Why it was refused. This goes back to the agent verbatim on the
    retry, so it has to say what is missing rather than that something
    is."""

    verified_by: str = ""
    """Who decided — a model, a person, an external system."""

    created_at: str = Field(default_factory=_generate_timestamp)
    """When the verification began. A verifier waiting on a person may
    have started days before it settled."""

    finished_at: str = Field(default_factory=_generate_timestamp)
    """When the verdict was reached."""


class StepRun(BaseModel):
    """What one step did in one run."""

    state: SOPStepState = SOPStepState.PENDING
    """Where the step stands."""

    submission: str = ""
    """The text the agent handed back. This is the only thing that
    crosses to the next step — no files, no context, no artifacts — so it
    outlives the step being skipped on later passes."""

    verifications: list[VerificationRecord] = Field(default_factory=list)
    """Every settled verdict, oldest first. Its length is the attempt
    count, and its last entry is the reason the agent is being asked to
    try again."""


class SOPRunState(BaseModel):
    """One execution of a SOP, and the whole of what it is worth saving.

    Named after :class:`~..state.AgentState` and playing the same part:
    the engine holds one, everything it knows lives in it, and handing a
    stored one back is how a run resumes.
    """

    sop_id: str
    """The SOP being run."""

    id: str = Field(default_factory=_generate_id)
    """The run identifier."""

    inputs: list[TextBlock | DataBlock] = Field(default_factory=list)
    """What the run was started with. Content rather than named values:
    nothing routes an input to a particular step, so it is simply what the
    first steps get to read — and being blocks, it carries images and
    files as readily as text."""

    steps: dict[str, StepRun] = Field(default_factory=dict)
    """``step_id`` → how that step is going."""

    created_at: str = Field(default_factory=_generate_timestamp)
    """When the run was created."""
