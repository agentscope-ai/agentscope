# -*- coding: utf-8 -*-
"""What a run of a SOP did.

One :class:`~._model.SOP` definition produces many runs. A definition
holds live agents and verifiers and cannot be written down; everything
here is plain data, and this is the half worth persisting — dump a run and
the progress outlives the process that made it.

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
from typing import Literal

from pydantic import BaseModel, Field

from ..message import DataBlock, TextBlock
from .._utils._common import _generate_id, _generate_timestamp

StepState = Literal[
    "pending",
    "running",
    "verifying",
    "awaiting_approval",
    "completed",
    "failed",
    "skipped",
]
"""Where a step stands in a run.

- ``pending``: not dispatched — either still blocked, or simply next up.
- ``running``: handed to its agent.
- ``verifying``: the agent submitted; the verifier has not answered yet.
- ``awaiting_approval``: the verifier said ``pending`` — it is waiting on
  something outside, most often a person.
- ``completed`` / ``failed``: settled. ``failed`` means the verifier kept
  refusing until ``max_attempts`` ran out.
- ``skipped``: unreachable, because something upstream failed.

Note there is no ``ready``: whether a step can start is recomputed from
:attr:`~._model.SOPStep.blocked_by` and the other steps' states.
"""

RunState = Literal["running", "completed", "failed"]
"""Where a run stands overall. Always derived — see
:func:`~._core.overall_state` — never stored."""


class VerificationRecord(BaseModel):
    """One settled verdict on a step.

    A verifier answering ``pending`` writes nothing here: nothing was
    judged, so no attempt was spent.
    """

    passed: bool
    """Whether the step was accepted."""

    message: str = ""
    """Why it was refused. This goes back to the agent verbatim on the
    retry, so it has to say what is missing rather than that something
    is."""

    verified_by: str = ""
    """Who decided — a model, a person, an external system."""

    checked_at: str = Field(default_factory=_generate_timestamp)
    """When the verdict was reached."""


class StepRun(BaseModel):
    """What one step did in one run."""

    state: StepState = "pending"
    """Where the step stands. See :data:`StepState`."""

    submission: str = ""
    """The text the agent handed back. This is the only thing that
    crosses to the next step — no files, no context, no artifacts — so it
    outlives the step being skipped on later passes."""

    verifications: list[VerificationRecord] = Field(default_factory=list)
    """Every settled verdict, oldest first. Its length is the attempt
    count, and its last entry is the reason the agent is being asked to
    try again."""


class SOPRun(BaseModel):
    """One execution of a SOP."""

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
