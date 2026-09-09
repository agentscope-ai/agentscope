# -*- coding: utf-8 -*-
"""Standard operating procedures — a fixed skeleton with free flesh.

The **skeleton** — which milestones there are, in what order, and what
each must prove — is written by a person and can be read without running
anything. The **flesh** — how any of it actually gets done — belongs to
the steps, and neither the SOP nor the engine sees it.

Two tests draw the boundary. *Is this a SOP?* — if you cannot say how
many steps it has and who does each without running it, no. *Should this
be a step?* — if nobody checks anything at that point, no.

One consequence is worth knowing before designing one: **a review is a
verifier, not a step.** A failed review sends work back to whoever
produced it, but a failed *step* retries *itself* — make "fact-check" a
step and a failure just re-runs the fact-checker to the same conclusion.

The engine sees **verdicts**, never **verifiers**. It walks the steps,
routes answers to whichever one parked, and spends the attempt budget;
how a step reached its verdict leaves no trace beyond the verdict. That
is what lets :class:`SOPStep` be a convenience rather than a law —
subclass :class:`SOPStepBase` and a step can be anything that fills in
its :class:`SOPStepRunState` on time.

A definition is code and holds no run state; a run is plain data. One
definition can drive any number of :class:`SOPEngine` instances, and a
run outlives the process it started in.

:class:`~..pipeline.GoalPipeline` is the one-step case of this: a goal
handed in at run time, verified until it passes. A SOP is what you write
when the milestones are known before the run is.
"""
from ._engine import SOPEngine
from ._schema import AgentLike, SOP, SOPStep, SOPStepBase
from ._state import (
    SOPPhase,
    SOPRunState,
    SOPStepRunState,
    VerificationResult,
)

__all__ = [
    # definition
    "SOP",
    "SOPStep",
    "SOPStepBase",
    "AgentLike",
    # run state
    "SOPRunState",
    "SOPStepRunState",
    "SOPPhase",
    "VerificationResult",
    # running it
    "SOPEngine",
]
