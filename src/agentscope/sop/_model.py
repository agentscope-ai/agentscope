# -*- coding: utf-8 -*-
"""The SOP definition model.

A SOP is authored once and run many times. Everything in this module is
part of that authored definition — it says what the procedure *is*, never
what any particular run *did*. The per-run side lives in :mod:`._run`.
"""
from typing import Literal

from pydantic import BaseModel, Field

from .._utils._common import _generate_id, _generate_timestamp

TriggerKind = Literal["manual", "agent", "schedule", "event"]
"""How a run is started. ``agent`` means another agent calls it (A2A)."""

WorkspacePolicy = Literal["per_run", "persistent", "none"]
"""The workspace every step of a run shares.

- ``per_run``: a fresh workspace per run — shared inside the run, never
  polluted between runs. The default.
- ``persistent``: one workspace behind every run, so files accumulate.
- ``none``: no shared filesystem; steps hand over text only.
"""

ExecutorMode = Literal["fixed_session", "per_run_session", "per_run_agent"]
"""How a step's executor relates to the SOP's runs.

- ``fixed_session``: one agent, **one session**, across every run. The
  conversation simply continues — along with its clutter.
- ``per_run_session``: one agent, a **new session each run**. What it
  learned carries over through the agent's long-term memory rather than
  its context.
- ``per_run_agent``: a **new agent** (and session) every run. Nothing
  carries over: fully reproducible, and it pays the ramp-up every time.
"""

AcceptanceKind = Literal["llm", "human"]
"""How a step is verified done.

Script-based checks are deliberately absent: they drag in "where does it
run" and "how is the environment installed", neither of which a SOP author
can answer from a form.
"""

ChannelKind = Literal["feishu", "discord"]
"""A messaging channel a step can notify."""


class AgentSpec(BaseModel):
    """An agent described inline, for the ``per_run_agent`` executor mode.

    The fields mirror the Create Agent form, so an author sees the same
    thing whether they build the agent here or over there.
    """

    name: str
    """The agent name."""

    system_prompt: str = ""
    """The system prompt."""

    model: str = ""
    """The chat model name."""


class Executor(BaseModel):
    """Who runs a step."""

    mode: ExecutorMode = "per_run_session"
    """How the executor relates to runs. See :data:`ExecutorMode`."""

    agent_id: str | None = None
    """The existing agent to use. Required unless ``mode`` is
    ``per_run_agent``."""

    spec: AgentSpec | None = None
    """The agent to build. Required when ``mode`` is ``per_run_agent``."""


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
    """Who signs off, for ``kind="human"``."""

    prompt: str | None = None
    """What the approver is asked, for ``kind="human"``."""


class NotifyTarget(BaseModel):
    """Where to post once a step finishes."""

    channel: ChannelKind
    """The messaging channel."""

    chat: str
    """The chat or group inside that channel."""


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

    notify: list[NotifyTarget] = Field(default_factory=list)
    """Where to post once it finishes."""

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

    trigger: TriggerKind = "manual"
    """How runs are started."""

    inputs: list[SOPInput] = Field(default_factory=list)
    """What a run must be given."""

    workspace: WorkspacePolicy = "per_run"
    """The workspace shared by the steps of a run. See
    :data:`WorkspacePolicy`."""

    allow_agent_chat: bool = False
    """Whether a step's agent may message another step's agent mid-run.

    Turning it on is convenient — a later step can ask an earlier one what
    it meant instead of guessing from the handover text — but it makes the
    step depend on another agent still being able to answer, which costs
    reproducibility.
    """

    steps: list[SOPStep] = Field(default_factory=list)
    """The steps. List order is for display only; execution order comes
    from :attr:`SOPStep.blocked_by`."""

    created_at: str = Field(default_factory=_generate_timestamp)
    """When the SOP was authored."""

    # ------------------------------------------------------------------
    # Engine-maintained. Not authored, but its lifetime spans every run,
    # so it cannot live on a single run either.
    # ------------------------------------------------------------------
    fixed_sessions: dict[str, str] = Field(default_factory=dict)
    """``step_id`` → the session reused by a ``fixed_session`` executor.
    Created on first use, then kept."""

    persistent_workspace_key: str | None = None
    """The long-lived workspace behind every run, when :attr:`workspace`
    is ``persistent``."""
