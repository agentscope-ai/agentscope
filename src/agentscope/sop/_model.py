# -*- coding: utf-8 -*-
"""The SOP definition.

Here a SOP is **code**: a step holds the :class:`~..agent.Agent` that will
run it, already built, with whatever model, tools and workspace you gave
it. There is no id to resolve and no spec to materialise, because at this
layer you are holding the object already::

    SOPStep(
        title="Write the code",
        agent=Agent(..., offloader=workspace),
        acceptance=Acceptance(criteria="Changes stay inside src/"),
    )

That makes a definition **not serialisable**, and deliberately so. A
service that stores SOPs keeps its own records — agent ids, model names,
the rest — and builds one of these before running it, exactly the way
``AgentData`` becomes a live ``Agent`` today. The run itself
(:mod:`._run`) stays plain data, and that is the half worth persisting.

Everything needing a service underneath stays out for the same reason: how
a run is **triggered**, which **workspace** its steps share, where a step
**notifies**, whether agents may **message each other**. There is no
scheduler, workspace manager, channel or message bus at this layer.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..agent import Agent
from .._utils._common import _generate_id, _generate_timestamp

AcceptanceKind = Literal["llm", "human"]
"""How a step is verified done.

Script-based checks are deliberately absent: they drag in "where does it
run" and "how is the environment installed", neither of which a SOP author
can answer from a form.
"""


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

    model_config = ConfigDict(arbitrary_types_allowed=True)
    """The step holds a live agent, which is not a pydantic type."""

    title: str
    """A short name for the step."""

    agent: Agent
    """The agent that runs this step, already built.

    Reuse one agent across several steps and they share its context and
    its workspace; give each its own and they do not. Run the same SOP
    twice with the same agent and its state carries over; build a fresh
    one per run and nothing does. All of that is decided here, in the
    construction — there is no mode to declare.
    """

    acceptance: Acceptance = Field(default_factory=Acceptance)
    """How the step is verified done."""

    id: str = Field(default_factory=_generate_id)
    """The step identifier."""

    instruction: str = ""
    """What the agent is asked to achieve. Appended to whatever its
    upstream steps handed over."""

    blocked_by: list[str] = Field(default_factory=list)
    """Ids of steps that must finish first. Empty means it can start as
    soon as the run does.

    Only this direction is stored; the reverse (``blocks``) is derived, so
    there is never a second copy to keep in sync.
    """

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

    model_config = ConfigDict(arbitrary_types_allowed=True)
    """Its steps hold live agents."""

    name: str
    """The SOP name."""

    steps: list[SOPStep] = Field(default_factory=list)
    """The steps. List order is for display only; execution order comes
    from :attr:`SOPStep.blocked_by`."""

    id: str = Field(default_factory=_generate_id)
    """The SOP identifier."""

    description: str = ""
    """What this procedure is for."""

    inputs: list[SOPInput] = Field(default_factory=list)
    """What a run must be given."""

    created_at: str = Field(default_factory=_generate_timestamp)
    """When the SOP was authored."""
