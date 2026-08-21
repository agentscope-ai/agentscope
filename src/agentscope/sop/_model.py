# -*- coding: utf-8 -*-
"""The SOP definition model.

A SOP is authored once and run many times. Everything here is part of that
authored definition — it says what the procedure *is*, never what any
particular run *did*. The per-run side lives in :mod:`._run`.

Kept out on purpose, because they only mean something once a service is
underneath: how a run gets **triggered** (manually, on a schedule, off an
inbound event), which **workspace** its steps share, where a finished step
**notifies**, and whether agents may **message each other** mid-run. None
of those exist at this layer — there is no scheduler, no workspace
manager, no channels, no message bus — so a service that grows them wraps
this definition rather than pushing the fields down into it.
"""
from typing import Literal

from pydantic import BaseModel, Field

from .._utils._common import _generate_id, _generate_timestamp

ExecutorMode = Literal["reuse_state", "reset_state", "new_agent"]
"""How a step's executor relates to the SOP's runs.

- ``reuse_state``: the same agent, keeping its state across every run. The
  conversation simply continues — along with its clutter.
- ``reset_state``: the same agent, starting from clean state each run.
  What it learned carries over through its long-term memory rather than
  its context.
- ``new_agent``: a fresh agent built from :attr:`Executor.spec` every run.
  Nothing carries over: fully reproducible, and it pays the ramp-up every
  time.
"""

AcceptanceKind = Literal["llm", "human"]
"""How a step is verified done.

Script-based checks are deliberately absent: they drag in "where does it
run" and "how is the environment installed", neither of which a SOP author
can answer from a form.
"""


class AgentSpec(BaseModel):
    """An agent described inline, for the ``new_agent`` executor mode.

    Everything here is a reference by name, not a live object, because a
    definition has to survive being written down.
    """

    name: str
    """The agent name."""

    system_prompt: str = ""
    """The system prompt."""

    model: str = ""
    """The chat model to use, by name."""


class Executor(BaseModel):
    """Who runs a step."""

    mode: ExecutorMode = "reset_state"
    """How the executor relates to runs. See :data:`ExecutorMode`."""

    agent_ref: str | None = None
    """Which existing agent to use. Opaque here — whoever drives the run
    decides what it points at. Required unless ``mode`` is
    ``new_agent``."""

    spec: AgentSpec | None = None
    """The agent to build. Required when ``mode`` is ``new_agent``."""


class Acceptance(BaseModel):
    """What it takes for a step to count as done.

    A step that does not pass is not marked complete: the reason goes back
    to its agent and it keeps working.
    """

    kind: AcceptanceKind = "llm"
    """How the step is judged."""

    criteria: str = ""
    """Free-form standard handed to the judge model, for ``kind="llm"``."""

    approver: str | None = None
    """Who signs off, for ``kind="human"``. How they are actually reached
    is the driver's problem."""

    prompt: str | None = None
    """What the approver is asked, for ``kind="human"``."""


class SOPStep(BaseModel):
    """One link in a SOP.

    A step states the destination, not the route: how to get there is left
    to the agent that runs it.
    """

    id: str = Field(default_factory=_generate_id)
    """The step identifier."""

    title: str
    """A short name for the step."""

    instruction: str = ""
    """What the executing agent is asked to achieve."""

    blocked_by: list[str] = Field(default_factory=list)
    """Ids of steps that must finish first. Empty means it can start as
    soon as the run does.

    Only this direction is stored; the reverse (``blocks``) is derived, so
    there is never a second copy to keep in sync.
    """

    executor: Executor = Field(default_factory=Executor)
    """Who runs the step."""

    acceptance: Acceptance = Field(default_factory=Acceptance)
    """How the step is verified done."""

    max_attempts: int = 3
    """How many failed acceptances before the step is given up on."""


class SOPInput(BaseModel):
    """A value the SOP asks for when a run is started."""

    name: str
    """The input name; runs supply values keyed by it."""

    description: str = ""
    """What the starter should provide."""


class SOP(BaseModel):
    """A reusable, step-by-step procedure with an acceptance gate on every
    step."""

    id: str = Field(default_factory=_generate_id)
    """The SOP identifier."""

    name: str
    """The SOP name."""

    description: str = ""
    """What this procedure is for."""

    inputs: list[SOPInput] = Field(default_factory=list)
    """What a run must be given."""

    steps: list[SOPStep] = Field(default_factory=list)
    """The steps. List order is for display only; execution order comes
    from :attr:`SOPStep.blocked_by`."""

    created_at: str = Field(default_factory=_generate_timestamp)
    """When the SOP was authored."""
