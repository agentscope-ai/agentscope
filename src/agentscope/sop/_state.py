# -*- coding: utf-8 -*-
"""The runtime state of a SOP run — the half worth persisting.

The engine reads and writes only what is here. How a step reached its
verdict is the step's own business and leaves no trace beyond the verdict
itself.
"""
from enum import StrEnum

from pydantic import BaseModel, Field

from ..message import Msg
from .._utils._common import _generate_id, _generate_timestamp


class SOPStepState(StrEnum):
    """Where a step stands in a run.

    There is no ``verifying``: whether a parked step stopped while working
    or while being judged is the step's business, not the engine's.
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


class SOPRunStatus(StrEnum):
    """Where a run stands overall. Always derived, never stored."""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING = "awaiting"
    COMPLETED = "completed"
    FAILED = "failed"


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
    """What one step did in one run."""

    step_id: str
    """The step this belongs to."""

    state: SOPStepState = SOPStepState.PENDING
    """Where the step stands."""

    given: list[Msg] = Field(default_factory=list)
    """What this attempt was handed to work from — the run's inputs for
    the first step, the one before's handover for the rest. Kept because
    the verifier needs it too: judging a draft means knowing what the
    step was asked for, not only what came back."""

    submission: str | None = None
    """What the current attempt handed over, once it has. ``None`` means
    the attempt has not produced anything yet — which is also how a step
    tells, on resume, that it parked while working rather than while
    being judged."""

    verifications: list[VerificationResult] = Field(default_factory=list)
    """Every settled verdict, oldest first. Its length is the attempt
    count, and its last entry is why the executor is being asked again."""


class SOPRunState(BaseModel):
    """One execution of a SOP, and the whole of what is worth saving.

    Assembled from the steps on the way out and handed back to them on the
    way in — see :meth:`~._engine.SOPEngine.state` and
    :meth:`~._engine.SOPEngine.load_state`.

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
    def status(self) -> SOPRunStatus:
        """Where the run stands, worked out from its steps."""
        states = [_.state for _ in self.steps.values()]
        if not states or all(_ is SOPStepState.PENDING for _ in states):
            return SOPRunStatus.PENDING
        if any(_ is SOPStepState.FAILED for _ in states):
            return SOPRunStatus.FAILED
        if all(_ is SOPStepState.COMPLETED for _ in states):
            return SOPRunStatus.COMPLETED
        if any(_ is SOPStepState.AWAITING for _ in states):
            return SOPRunStatus.AWAITING
        return SOPRunStatus.RUNNING
