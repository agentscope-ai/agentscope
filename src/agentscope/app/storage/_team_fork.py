# -*- coding: utf-8 -*-
"""Helpers for forking Team graphs with created members."""

from dataclasses import dataclass
from datetime import datetime

from ._exceptions import (
    SessionForkConflictError,
    SessionForkCorruptedGraphError,
    SessionForkNotFoundError,
)
from ._model import AgentRecord, SessionRecord, SessionSource, TeamMember
from ._model import TeamRecord


@dataclass(frozen=True)
class TeamMemberForkPlan:
    """Serialized Agent, Session, and raw messages for one member."""

    source_member: TeamMember
    target_agent_id: str
    target_session_id: str
    target_agent_payload: str
    target_session_payload: str
    source_messages: tuple[str | bytes, ...]


@dataclass(frozen=True)
class TeamForkPlan:
    """Immutable Redis write plan for a created-member Team fork."""

    forked_at: datetime
    source_session_id: str
    target_team_id: str
    target_leader_agent_id: str
    target_leader_session_id: str
    target_team_payload: str
    target_leader_session_payload: str
    target_leader_messages: tuple[str | bytes, ...]
    target_members: tuple[TeamMemberForkPlan, ...]
    exclusive_target_keys: tuple[str, ...]
    shared_index_keys: tuple[str, ...]


def validate_team_source_identity(
    session: SessionRecord,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> None:
    """Validate a Team source before accessing its TeamRecord."""
    if session.id != session_id:
        raise SessionForkCorruptedGraphError(
            "The source session record does not match its storage key.",
        )
    if session.user_id != user_id:
        raise SessionForkNotFoundError(
            "The source session does not belong to the requested user.",
        )
    if session.agent_id != agent_id:
        raise SessionForkNotFoundError(
            "The source session does not belong to the requested agent.",
        )
    if session.team_id is None:
        raise SessionForkCorruptedGraphError(
            "The source session is not attached to a Team.",
        )
    if (
        session.source != SessionSource.USER
        or session.source_schedule_id is not None
    ):
        raise SessionForkCorruptedGraphError(
            "Team session provenance is inconsistent.",
        )


def validate_team_members(
    team: TeamRecord,
    source_session_id: str,
) -> tuple[TeamMember, ...]:
    """Validate roles, duplicate references, and Leader membership."""
    seen_agents: set[str] = set()
    seen_sessions: set[str] = set()
    source_is_member = False
    for member in team.data.members:
        if member.role == "invited":
            raise SessionForkConflictError(
                "Teams with invited members are not supported yet.",
            )
        if member.role != "created":
            raise SessionForkCorruptedGraphError(
                "The Team contains an unknown member role.",
            )
        if member.agent_id in seen_agents:
            raise SessionForkCorruptedGraphError(
                "The Team contains duplicate member agent ids.",
            )
        if member.session_id in seen_sessions:
            raise SessionForkCorruptedGraphError(
                "The Team contains duplicate member session ids.",
            )
        seen_agents.add(member.agent_id)
        seen_sessions.add(member.session_id)
        source_is_member |= member.session_id == source_session_id

    is_leader = team.session_id == source_session_id
    if is_leader and source_is_member:
        raise SessionForkCorruptedGraphError(
            "The source session is both Team leader and member.",
        )
    if not is_leader and source_is_member:
        raise SessionForkConflictError(
            "Worker sessions cannot be forked.",
        )
    if not is_leader:
        raise SessionForkCorruptedGraphError(
            "The source session is not the Team leader.",
        )
    return tuple(team.data.members)


def validate_created_member_resources(
    member: TeamMember,
    agent: AgentRecord,
    session: SessionRecord,
    team: TeamRecord,
) -> None:
    """Validate one created member's Agent and Session relationships."""
    if agent.id != member.agent_id or agent.user_id != team.user_id:
        raise SessionForkCorruptedGraphError(
            "A Team member Agent does not match its graph entry.",
        )
    if session.id != member.session_id or session.user_id != member.owner_id:
        raise SessionForkCorruptedGraphError(
            "A Team member Session does not match its graph entry.",
        )
    if session.agent_id != agent.id or session.team_id != team.id:
        raise SessionForkCorruptedGraphError(
            "A Team member Session does not match its graph entry.",
        )
    if (
        session.source != SessionSource.USER
        or session.source_schedule_id is not None
    ):
        raise SessionForkCorruptedGraphError(
            "A Team member Session has invalid provenance.",
        )
