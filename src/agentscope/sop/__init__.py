# -*- coding: utf-8 -*-
"""Standard operating procedures — a fixed skeleton with free flesh.

The **skeleton** — which milestones there are, in what order, and what
each must prove — is written by a person and can be read without running
anything. The **flesh** — how any of it actually gets done — belongs to
the agents, and the SOP neither sees it nor prescribes it.

In one line: *a fixed sequence of milestones, each of which must be
verified before the next can start; how a milestone is reached is nobody
else's business.*

Three things follow, and between them they explain every decision here.

- **A step is a checkpoint somebody cares about, not a unit of work.**
  "Establish what happened" is one step even if it means reading four
  systems.
- **Verification is the whole point, not a feature.** An agent's output
  is uncertain, so "claims to be done" and "is done" have to be different
  things — which a workflow engine never needs, its nodes being code.
- **Being fixed is where the value comes from.** Auditability, reuse and
  predictability all follow from it.

Two tests draw the boundary. *Is this a SOP?* — if you cannot say how many
steps it has and who does each without running it, no. *Should this be a
step?* — if nobody checks anything at that point, no. The first rules out
runtime fan-out, computed branching and steps that appear as you go; the
second rules out lifting an agent's own breakdown into the procedure.

One consequence is worth knowing before designing one: **a review is a
verifier, not a step.** A failed review sends work back to whoever
produced it, but a failed *step* retries *itself* — make "fact-check" a
step and a failure just re-runs the fact-checker to the same conclusion.

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
from ._state import (
    SOPRunState,
    SOPRunStatus,
    SOPStepState,
    StepRun,
    VerificationRecord,
)
from ._sop import SOP, SOPStep
from ._verifier import VerifierBase

__all__ = [
    # definition
    "SOP",
    "SOPStep",
    # verification
    "VerifierBase",
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
