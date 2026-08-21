# -*- coding: utf-8 -*-
"""The SOP runtime model.

One :class:`SOP` definition produces many :class:`SOPRun` instances. This
module holds only what a particular run did — its state machine, what each
step handed back, and how each acceptance went.
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
- ``running``: handed to its executor.
- ``verifying``: the executor submitted; acceptance is being judged.
- ``awaiting_approval``: waiting on a person.
- ``completed`` / ``failed``: settled. ``failed`` means acceptance kept
  refusing until ``max_attempts`` ran out.
- ``skipped``: unreachable because something upstream failed.

Note there is no ``ready``: whether a step can start is recomputed from
:attr:`~.SOPStep.blocked_by` and the other steps' states, never stored.
"""

RunState = Literal["running", "completed", "failed", "cancelled"]
"""Where a run stands overall."""


class VerificationRecord(BaseModel):
    """One acceptance attempt."""

    attempt: int
    """Which attempt this was, counting from 1."""

    passed: bool
    """Whether the step was accepted."""

    message: str = ""
    """Why it was refused. This is what goes back to the agent, so it has
    to say what is missing, not just that something is."""

    verified_by: str = ""
    """Who judged: a model name, or the approver."""

    checked_at: str = Field(default_factory=_generate_timestamp)
    """When the judgement was made."""


class StepRun(BaseModel):
    """What one step did in one run."""

    step_id: str
    """The :class:`~.SOPStep` this belongs to."""

    state: StepState = "pending"
    """Where the step stands. See :data:`StepState`."""

    attempts: int = 0
    """How many times acceptance has been attempted."""

    executor_ref: str | None = None
    """Which agent actually ran it, by name. Recorded so a finished run
    still says who did what once the agents themselves are gone."""

    submission: str = ""
    """The text the agent submitted as its result. This is the only thing
    that crosses to the next step: no files, no context, no artifacts."""

    verifications: list[VerificationRecord] = Field(default_factory=list)
    """Every acceptance attempt, oldest first."""

    started_at: str | None = None
    """When the step was dispatched."""

    finished_at: str | None = None
    """When the step settled."""


class SOPRun(BaseModel):
    """One execution of a SOP."""

    id: str = Field(default_factory=_generate_id)
    """The run identifier."""

    sop_id: str
    """The SOP being run."""

    state: RunState = "running"
    """Where the run stands."""

    inputs: list[TextBlock | DataBlock] = Field(default_factory=list)
    """What the run was started with. Content rather than named values:
    nothing routes an input to a particular step, so it is simply what the
    first steps get to read — and being blocks, it can carry images and
    files as easily as text."""

    steps: list[StepRun] = Field(default_factory=list)
    """One entry per step of the SOP."""

    created_at: str = Field(default_factory=_generate_timestamp)
    """When the run was created."""

    finished_at: str | None = None
    """When the run settled."""

    def step(self, step_id: str) -> StepRun | None:
        """Return the record for ``step_id``, or ``None`` if this run has
        no such step.

        Args:
            step_id (`str`):
                The step to look up.

        Returns:
            `StepRun | None`:
                The matching record.
        """
        return next((s for s in self.steps if s.step_id == step_id), None)
