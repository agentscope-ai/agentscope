# -*- coding: utf-8 -*-
"""Standard operating procedures — fixed, reusable, step-by-step
procedures with a verifier on every step.

A SOP fixes the skeleton and leaves the flesh to the agents: the order of
the steps and what each must prove are authored by a person, while how any
one step gets done is left entirely to the agent that runs it.

Three pieces, deliberately separate:

- :class:`SOP` and :class:`SOPStep` are the **definition**, and at this
  layer it is code: a step holds the agent that runs it and the verifier
  that judges it, both already built.
- :class:`SOPRunState` is one **run** — what actually happened. Plain
  data, and the half worth persisting. It holds no cursor: a step keeps
  only what cannot be worked out again, and every pass recomputes what
  can proceed.
- :class:`SOPEngine` runs it, shaped like an agent. Feed it, watch the
  events, and when something needs a person the stream simply ends —
  nothing stays suspended.

Steps hand over text and nothing else. There are no artifacts to declare:
an agent submits its result with :class:`SubmitStepResult`, and the next
step reads that.

Everything needing a service underneath stays out: triggers and schedules,
workspace allocation, notification channels, agent-to-agent messaging, and
persistence. There is no scheduler, workspace manager, channel or message
bus here. A service that has them keeps its own records and builds one of
these definitions before running it — the way ``AgentData`` becomes a live
``Agent`` today — rather than pushing its fields down into this layer.
"""

from ._engine import (
    Action,
    Dispatch,
    Judge,
    RunSettledEvent,
    Settle,
    SOPEngine,
    SOPEvent,
    StepStateEvent,
    SubmitStepResult,
    feedback,
    find_step,
    is_ready,
    new_run,
    next_actions,
    overall_status,
    upstream_submissions,
)
from ._run import (
    SOPRunState,
    SOPRunStatus,
    SOPStepState,
    StepRun,
    VerificationRecord,
)
from ._sop import SOP, SOPStep
from ._verifier import CallbackVerifier, VerifierBase

__all__ = [
    # definition
    "SOP",
    "SOPStep",
    # verification
    "VerifierBase",
    "CallbackVerifier",
    "VerificationRecord",
    # runtime
    "SOPRunState",
    "StepRun",
    "SOPStepState",
    "SOPRunStatus",
    # running it
    "SOPEngine",
    "SubmitStepResult",
    "StepStateEvent",
    "RunSettledEvent",
    "SOPEvent",
    # decisions
    "new_run",
    "next_actions",
    "is_ready",
    "overall_status",
    "upstream_submissions",
    "feedback",
    "find_step",
    "Action",
    "Dispatch",
    "Judge",
    "Settle",
]
