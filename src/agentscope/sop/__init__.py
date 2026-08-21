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

Everything that needs a service underneath stays out: triggers and
schedules, workspace allocation, notification channels, agent-to-agent
messaging, and persistence. There is no scheduler, workspace manager,
channel or message bus at this layer, so a service that has them wraps
this definition rather than pushing its own fields down into it.
"""

from . import _core as core
from ._core import (
    Action,
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
    Executor,
    ExecutorMode,
    SOPInput,
    SOPStep,
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
    "ExecutorMode",
    "AcceptanceKind",
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
    "Settle",
]
