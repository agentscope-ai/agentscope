# -*- coding: utf-8 -*-
"""Standard operating procedures — fixed, reusable, step-by-step
procedures with an acceptance gate on every step.

A SOP fixes the skeleton and leaves the flesh to the agents: the order of
the steps and what each must prove are authored by a person, while how any
one step gets done is left entirely to the agent that runs it.

Three layers, deliberately separate:

- :class:`SOP` and :class:`SOPStep` are the **definition** — written once,
  run many times.
- :class:`SOPRun` and :class:`StepRun` are one **run** — what actually
  happened.
- :mod:`._core` decides **what to do next**, as pure functions over those
  two. It is shared by every driver, so readiness, retries and failure
  spreading behave the same wherever a SOP runs.

Steps hand over text and nothing else. There are no artifacts to declare:
an agent submits its result as text, and the next step reads it.

Persistence is not this layer's business — a run lives in memory here, and
the service keeps its own.
"""

from . import _core as core
from ._core import (
    Action,
    Announce,
    AskApproval,
    Dispatch,
    Judge,
    PollApproval,
    Settle,
    new_run,
    next_actions,
)
from ._model import (
    SOP,
    Acceptance,
    AcceptanceKind,
    AgentSpec,
    ChannelKind,
    Executor,
    ExecutorMode,
    NotifyTarget,
    SOPInput,
    SOPStep,
    TriggerKind,
    WorkspacePolicy,
)
from ._run import (
    RunState,
    SOPRun,
    StepRun,
    StepState,
    VerificationRecord,
)

__all__ = [
    # definition
    "SOP",
    "SOPStep",
    "SOPInput",
    "Executor",
    "AgentSpec",
    "Acceptance",
    "NotifyTarget",
    "TriggerKind",
    "WorkspacePolicy",
    "ExecutorMode",
    "AcceptanceKind",
    "ChannelKind",
    # runtime
    "SOPRun",
    "StepRun",
    "VerificationRecord",
    "RunState",
    "StepState",
    # decisions
    "core",
    "new_run",
    "next_actions",
    "Action",
    "Dispatch",
    "Judge",
    "AskApproval",
    "PollApproval",
    "Announce",
    "Settle",
]
