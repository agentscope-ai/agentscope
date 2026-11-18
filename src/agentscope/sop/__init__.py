# -*- coding: utf-8 -*-
"""Standard operating procedures — a fixed sequence of milestones.

A :class:`SOP` says which steps there are and what each must prove; how
a step gets there is its own business. :class:`SOPEngine` walks them,
routes answers to whichever one parked, and records verdicts in a
:class:`SOPRunState` that outlives the process.

A definition holds no run state, so one can drive any number of runs.
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
