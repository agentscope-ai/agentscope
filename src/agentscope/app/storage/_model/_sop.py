# -*- coding: utf-8 -*-
"""The SOP storage classes — a procedure's description, and its runs.

A :class:`~agentscope.sop.SOP` holds live agents, so it is code and
never lands in a database. What is stored is the description the
service rebuilds one from, and the run state the SDK already models.
"""
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from ._base import _RecordBase
from ....sop import SOPRunState


class SOPWorkspaceGrain(StrEnum):
    """How many workspaces a run gets."""

    RUN = "run"
    """One for the whole run, so steps can hand files to each other."""

    PER_SESSION_KEY = "per_session_key"
    """One per session, so no step sees another's files."""


class SOPAgentRef(BaseModel):
    """Which agent does something, and in which conversation."""

    agent_id: str = Field(description="The agent that does the work.")

    session_key: str = Field(
        description=(
            "Which conversation it does it in. Two references sharing a "
            "key share one session, and therefore one context; distinct "
            "keys keep them apart even for the same agent."
        ),
    )


class AgentVerifier(BaseModel):
    """A verifier that is itself an agent."""

    type: Literal["agent"] = "agent"

    agent: SOPAgentRef = Field(description="Who judges the work.")

    criteria: str = Field(
        default="",
        description=(
            "What to hold the work to, beyond the step's own "
            "description — which the verifier is shown regardless. "
            "Lives here rather than in the agent's system prompt "
            "because one reviewer can serve several steps at "
            "different bars."
        ),
        json_schema_extra={"format": "textarea"},
    )


class HumanVerifier(BaseModel):
    """A verifier that asks a person and waits for the answer."""

    type: Literal["human"] = "human"

    question: str = Field(
        default="Does this meet what the step had to prove?",
        description="What the person is asked.",
        json_schema_extra={"format": "textarea"},
    )


# Who judges a step. A tagged union because judging is not an agent's
# job in particular — a person does it, and so will a script.
SOPVerifier = Annotated[
    Union[AgentVerifier, HumanVerifier],
    Field(discriminator="type"),
]


class SOPStepDataV1(BaseModel):
    """One milestone, as :class:`~agentscope.sop.SOPStep` runs it.

    Tagged with :attr:`version` from the start. A step whose runtime
    stops being able to run what an older one wrote becomes its own
    model beside this one, discriminated on that tag, rather than a
    round of optional fields on this one.
    """

    version: Literal["v1"] = "v1"

    subject: str = Field(description="A brief, actionable name.")

    description: str = Field(
        description="What this step must achieve — the destination.",
    )

    executor: SOPAgentRef = Field(description="Who does the work.")

    verifier: SOPVerifier | None = Field(
        default=None,
        description=(
            "Who judges it. ``None`` accepts whatever comes back, which "
            "is right for a step that only has to happen."
        ),
    )

    max_attempts: int = Field(
        default=3,
        gt=0,
        description="How many refusals before the run gives up on it.",
    )


class SOPData(BaseModel):
    """A procedure: its milestones, in order."""

    name: str = Field(description="The name of the procedure.")

    description: str = Field(
        default="",
        description="What the procedure is for.",
    )

    steps: list[SOPStepDataV1] = Field(
        description="Its milestones, in the order they must happen.",
    )

    workspace_grain: SOPWorkspaceGrain = Field(
        default=SOPWorkspaceGrain.RUN,
        description="How many workspaces a run of this gets.",
    )


class SOPRecord(_RecordBase):
    """A stored procedure."""

    user_id: str
    """The user id."""

    data: SOPData
    """The procedure."""


class SOPRunRecord(_RecordBase):
    """One run of a procedure."""

    user_id: str
    """The user id."""

    sop_id: str
    """The :class:`SOPRecord` this run came from."""

    definition: SOPData
    """The procedure as it read when the run started.

    A copy rather than a lookup: editing a SOP would otherwise strand
    every run of it that is still waiting on someone, since the engine
    refuses a state whose steps no longer line up."""

    sessions: dict[str, str] = Field(default_factory=dict)
    """Which session each of :attr:`SOPAgentRef.session_key` names.

    Keyed by the key rather than by agent or by step, so a step that
    grows a third role needs nothing here."""

    state: SOPRunState = Field(default_factory=SOPRunState)
    """How the run is going. The SDK's own model, stored as it is."""
