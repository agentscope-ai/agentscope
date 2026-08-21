# -*- coding: utf-8 -*-
"""The SOP definition.

Here a SOP is **code**: a step holds the :class:`~..agent.Agent` that will
run it and the :class:`~._verifier.VerifierBase` that decides when it is
done, both already built. There is no id to resolve and no spec to
materialise, because at this layer you are holding the objects already::

    SOPStep(
        subject="Write the code",
        description="Implement the plan. TDD is up to you.",
        agent=Agent(..., offloader=workspace),
        verifier=CallbackVerifier(tests_pass),
    )

That makes a definition **not serialisable**, and deliberately so. A
service that stores SOPs keeps its own records — agent ids, model names,
the rest — and builds one of these before running it, exactly the way
``AgentData`` becomes a live ``Agent`` today. The run itself
(:mod:`._run`) stays plain data, and that is the half worth persisting.

A run's input is not modelled here either. With nothing routing values to
particular steps, naming them buys nothing — whatever a run is started
with becomes content the first steps read, so it is an argument to the
engine and a record on the run, not a field on the definition.

Everything needing a service underneath stays out for the same reason: how
a run is **triggered**, which **workspace** its steps share, where a step
**notifies**, whether agents may **message each other**. There is no
scheduler, workspace manager, channel or message bus at this layer.
"""
from pydantic import BaseModel, ConfigDict, Field

from ._verifier import VerifierBase
from ..agent import Agent
from ..state import Task
from .._utils._common import _generate_id, _generate_timestamp


class SOPStep(BaseModel):
    """One link in a SOP.

    A step states the destination, not the route: how to get there is left
    to the agent that runs it. The field names follow
    :class:`~..state.Task` — a step is the same idea one level up.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    """The step holds a live agent and verifier, neither a pydantic type."""

    subject: str
    """A brief, actionable name for the step."""

    agent: Agent
    """The agent that runs this step, already built.

    Reuse one agent across several steps and they share its context and
    its workspace; give each its own and they do not. Run the same SOP
    twice with the same agent and its state carries over; build a fresh
    one per run and nothing does. All of that is decided here, in the
    construction — there is no mode to declare.
    """

    verifier: VerifierBase | None = None
    """Decides when the step is done. ``None`` accepts whatever the agent
    submits, which is right for a step that only has to happen."""

    id: str = Field(default_factory=_generate_id)
    """The step identifier."""

    description: str = ""
    """What the agent is asked to achieve — the goal, not the route."""

    tasks: list[Task] = Field(default_factory=list)
    """Planning tasks to seed the agent's own task list with.

    Empty by default, which leaves the agent to decompose the step
    however it likes. Filling it in narrows that freedom without taking it
    away: the agent still owns the list once the step starts, and may add
    to it or strike things off as it learns.
    """

    blocked_by: list[str] = Field(default_factory=list)
    """Ids of steps that must finish first. Empty means it can start as
    soon as the run does.

    Only this direction is stored; the reverse (``blocks``) is derived, so
    there is never a second copy to keep in sync.
    """

    max_attempts: int = 3
    """How many refusals before the step is given up on."""


class SOP(BaseModel):
    """A reusable, step-by-step procedure with a verifier on every step."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    """Its steps hold live agents and verifiers."""

    name: str
    """The SOP name."""

    steps: list[SOPStep] = Field(default_factory=list)
    """The steps. List order is for display only; execution order comes
    from :attr:`SOPStep.blocked_by`."""

    id: str = Field(default_factory=_generate_id)
    """The SOP identifier."""

    description: str = ""
    """What this procedure is for."""

    created_at: str = Field(default_factory=_generate_timestamp)
    """When the SOP was authored."""
