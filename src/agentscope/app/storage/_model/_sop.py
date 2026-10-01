# -*- coding: utf-8 -*-
"""The SOP storage classes: a procedure's description, and its runs.
A live :class:`~agentscope.sop.SOP` holds agents, so only this is stored."""
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from ._base import _RecordBase
from ._channel import SessionSettings
from ....sop import SOPRunState


class SOPWorkspaceGrain(StrEnum):
    """How many workspaces a run gets. Always minted for the run, never
    shared with another run."""

    RUN = "run"
    """One for the whole run, so steps can hand files to each other."""

    PER_SESSION_KEY = "per_session_key"
    """One per session, so no step sees another's files."""


class SOPAgentRef(BaseModel):
    """Which agent does something, and in which conversation."""

    agent_id: str = Field(description="The agent that does the work.")

    session_key: str = Field(
        description=(
            "Which conversation it does it in; references sharing a key "
            "share one session."
        ),
    )


class AgentVerifier(BaseModel):
    """A verifier that is itself an agent."""

    type: Literal["agent"] = "agent"

    agent: SOPAgentRef = Field(description="Who judges the work.")

    criteria: str = Field(
        default="",
        description=(
            "What to hold the work to, beyond the step's own description."
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


# Who judges a step: an agent or a person.
SOPVerifier = Annotated[
    Union[AgentVerifier, HumanVerifier],
    Field(discriminator="type"),
]


class SOPStepDataV1(BaseModel):
    """One milestone, as :class:`~agentscope.sop.SOPStep` runs it.
    An incompatible change becomes a new model discriminated on
    :attr:`version`."""

    version: Literal["v1"] = "v1"

    subject: str = Field(description="A brief, actionable name.")

    description: str = Field(
        description="What this step must achieve — the destination.",
    )

    executor: SOPAgentRef = Field(description="Who does the work.")

    verifier: SOPVerifier | None = Field(
        default=None,
        description="Who judges it. ``None`` accepts whatever comes back.",
    )

    max_attempts: int = Field(
        default=3,
        gt=0,
        description="How many refusals before the run gives up on it.",
    )


def _session_agents(steps: list[SOPStepDataV1]) -> dict[str, str]:
    """Map each session key the steps name to its agent.

    Raises:
        `ValueError`:
            If one key is used by two different agents.
    """
    agents: dict[str, str] = {}
    for step in steps:
        refs = [step.executor]
        if isinstance(step.verifier, AgentVerifier):
            refs.append(step.verifier.agent)
        for ref in refs:
            if (
                agents.setdefault(ref.session_key, ref.agent_id)
                != ref.agent_id
            ):
                raise ValueError(
                    f"Conversation {ref.session_key!r} is used by more "
                    f"than one agent.",
                )
    return agents


class SOPData(BaseModel):
    """A procedure: its milestones, in order."""

    name: str = Field(description="The name of the procedure.")

    description: str = Field(
        default="",
        description="What the procedure is for.",
    )

    steps: list[SOPStepDataV1] = Field(
        min_length=1,
        description="Its milestones, in the order they must happen.",
    )

    workspace_grain: SOPWorkspaceGrain = Field(
        default=SOPWorkspaceGrain.RUN,
        description="How many workspaces a run of this gets.",
    )

    session_settings: dict[str, SessionSettings] = Field(
        default_factory=dict,
        description=(
            "What each conversation is opened with, keyed by session key."
        ),
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

    sop_id: str = Field(frozen=True)
    """The :class:`SOPRecord` this run came from. Frozen, since backends
    index runs under it."""

    definition: SOPData
    """A copy of the procedure at start, so editing it cannot strand
    the run."""

    sessions: dict[str, str] = Field(default_factory=dict)
    """The session id for each :attr:`SOPAgentRef.session_key`."""

    state: SOPRunState = Field(default_factory=SOPRunState)
    """How the run is going."""
