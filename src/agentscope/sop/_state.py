# -*- coding: utf-8 -*-
"""The runtime state of a SOP run — the half worth persisting.

A definition is code and can be run any number of times; a run is plain
data and belongs to exactly one of those times. The engine owns the run
and hands each step the slice that is its own, so nothing about a run
ever lives on a step object.
"""
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ..message import DataBlock, Msg, TextBlock
from .._utils._common import _generate_id, _generate_timestamp


class SOPPhase(StrEnum):
    """Where a step, or a whole run, stands.

    One enum for both: a run is only ever as far along as its steps let
    it be.
    """

    PENDING = "pending"
    """Not started, or sent back to try again."""

    RUNNING = "running"
    """In flight."""

    AWAITING = "awaiting"
    """Parked — someone outside has to answer before it can go on."""

    COMPLETED = "completed"
    """Accepted."""

    FAILED = "failed"
    """Refused until :attr:`~._schema.SOPStepBase.max_attempts` ran out."""


class VerificationResult(BaseModel):
    """One settled verdict on one attempt.

    Only settled verdicts exist — a step with nothing to say yet records
    nothing, because a verdict that has not happened is not a verdict.
    """

    passed: bool
    """Whether the attempt was accepted."""

    message: str = ""
    """Why it was refused. This goes back to the executor verbatim on the
    next attempt, so it has to say what is missing rather than that
    something is."""

    verifier: str = ""
    """Who decided — a model, a person, an external system."""

    created_at: str = Field(default_factory=_generate_timestamp)
    """When the verdict was reached."""


class SOPStepRunState(BaseModel):
    """What one step did in one run.

    The engine writes :attr:`given`; the step keeps the other three
    honest. To remember more, subclass this and name the subclass in
    :attr:`~._schema.SOPStepBase.state_type` — extra fields survive a
    round trip through storage.
    """

    model_config = ConfigDict(extra="allow")

    step_id: str
    """The step this belongs to."""

    phase: SOPPhase = SOPPhase.PENDING
    """Where the step stands."""

    given: list[Msg] = Field(default_factory=list)
    """What the engine dispatched this attempt with — the run's inputs
    for the first step, the one before's handover for the rest. Written
    by the engine, read by the step: a verifier judging a draft has to
    see what was asked for, and after a park the answer that resumes the
    attempt is all the step is handed."""

    submission: list[TextBlock | DataBlock] | None = None
    """What the current attempt handed over, once it has.

    Blocks rather than text: a step that produces a chart, a file or an
    image hands it on as readily as a sentence, and the next step reads
    it the same way. ``None`` — as opposed to empty — means the attempt
    has not produced anything yet, which is also how a step tells, on
    resume, that it parked while working rather than while being
    judged."""

    verifications: list[VerificationResult] = Field(default_factory=list)
    """Every settled verdict, oldest first. Its length is the attempt
    count, and its last entry is why the executor is being asked again."""


class SOPRunState(BaseModel):
    """One execution of a SOP, and the whole of what is worth saving.

    It covers the SOP's own state and nothing below it: an executor that
    keeps state of its own (an :class:`~..agent.Agent` does) is persisted
    by whoever built it, the same way it is built.
    """

    sop_id: str
    """The SOP being run."""

    id: str = Field(default_factory=_generate_id)
    """The run identifier."""

    inputs: list[Msg] = Field(default_factory=list)
    """What the run was started with, and what its first step reads."""

    steps: dict[str, SOPStepRunState] = Field(default_factory=dict)
    """``step_id`` → how that step is going."""

    created_at: str = Field(default_factory=_generate_timestamp)
    """When the run was created."""

    @property
    def phase(self) -> SOPPhase:
        """Where the run stands, worked out from its steps."""
        phases = [_.phase for _ in self.steps.values()]
        if not phases or all(_ is SOPPhase.PENDING for _ in phases):
            return SOPPhase.PENDING
        if any(_ is SOPPhase.FAILED for _ in phases):
            return SOPPhase.FAILED
        if all(_ is SOPPhase.COMPLETED for _ in phases):
            return SOPPhase.COMPLETED
        if any(_ is SOPPhase.AWAITING for _ in phases):
            return SOPPhase.AWAITING
        return SOPPhase.RUNNING
