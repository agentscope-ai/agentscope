# -*- coding: utf-8 -*-
"""The team storage class."""
from typing import Literal

from pydantic import BaseModel, Field

from ._base import _RecordBase


class TeamMember(BaseModel):
    """An entry in a team's member roster.

    Unlike the legacy :attr:`TeamData.member_ids`, this carries both the
    agent id AND the session id — required because an *invited* member's
    agent (e.g. Monday) can have multiple sessions of its own; the team
    only wants the one it created when the leader called ``AgentInvite``.
    The ``role`` tag then drives cascade behavior (see
    :meth:`SessionService.delete_team`): ``created`` members are fully
    deleted with the team, ``invited`` members only lose the borrowed
    session while their :class:`AgentRecord` survives.
    """

    owner_id: str = Field(
        description=(
            "Owner of the member's agent. Always equals the team owner "
            "in today's user-only invite pool, but is stored explicitly "
            "so a future admin-share layer (agents borrowed across users) "
            "can slot in without a schema migration. Distinct name from "
            ":attr:`TeamRecord.user_id` on purpose — the surrounding "
            "team already carries the team owner in context, so calling "
            "this field ``user_id`` too would be ambiguous."
        ),
    )

    agent_id: str = Field(
        description="The member agent's id.",
    )

    session_id: str = Field(
        description=(
            "The team-scoped session id for this member. For ``created`` "
            "members this is the sole session (1:1 with the agent). For "
            "``invited`` members this is the freshly-minted session that "
            "``AgentInvite`` created — the agent's other sessions are "
            "unrelated to this team."
        ),
    )

    role: Literal["created", "invited"] = Field(
        description=(
            "How this member joined the team. ``created`` — spawned "
            "from a :class:`SubAgentTemplate` via ``AgentCreate``, "
            "deleted with the team. ``invited`` — a pre-existing "
            "user-owned agent borrowed via ``AgentInvite``, retains its "
            ":class:`AgentRecord` when the team is dissolved."
        ),
    )


class TeamData(BaseModel):
    """The team data model."""

    name: str = Field(
        description="Display name of the team.",
        title="Name",
    )

    description: str = Field(
        default="",
        description=(
            "What the team is for — its overall goal or shared context. "
            "Wired into every member's system prompt so all members share "
            "the same high-level understanding of why the team exists."
        ),
        title="Description",
    )

    member_ids: list[str] = Field(
        default_factory=list,
        description=(
            "**Deprecated** — legacy roster of worker agent ids from "
            "before the ``AgentInvite`` era, when every member was "
            "team-spawned (``source='team'``) with a 1:1 agent-session "
            "mapping. New code should read :attr:`members` via the "
            "``ensure_team_members`` helper, which migrates any records "
            "still using this field to the richer schema on first read."
        ),
        title="Member Ids",
        deprecated=True,
    )

    members: list[TeamMember] = Field(
        default_factory=list,
        description=(
            "Explicit member roster with role + session id per entry. "
            "Read this via the ``ensure_team_members`` helper so legacy "
            "records with only :attr:`member_ids` populated are migrated "
            "transparently."
        ),
        title="Members",
    )


class TeamRecord(_RecordBase):
    """The team ORM model.

    Team membership is session-level: the leader is identified by its
    ``session_id`` (since a user agent can lead multiple teams across
    different sessions). Workers are identified by
    :class:`TeamMember` entries in :attr:`TeamData.members`, accessed
    via the ``ensure_team_members`` helper which handles migration from
    the legacy :attr:`TeamData.member_ids` shape.
    """

    user_id: str
    """The user id."""

    session_id: str
    """The leader session id — the session that called ``create_team``."""

    data: TeamData
    """The team data."""
