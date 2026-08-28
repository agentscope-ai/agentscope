# -*- coding: utf-8 -*-
"""The utils for storage."""
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, SecretStr

from ._model import TeamMember

if TYPE_CHECKING:
    from ._base import StorageBase
    from ._model import AgentRecord, TeamRecord


def _dump_with_secrets(model: BaseModel) -> dict:
    """Dump the BaseModel instance with SecretStr fields. Used for
    storage.

    Args:
        model (`BaseModel`):
            The model instance to dump.

    Returns:
        `dict`:
            The dumped JSON with secrets included.
    """
    # Use mode='json' so that Pydantic converts non-JSON-native types
    # (e.g. datetime, UUID) to their JSON-compatible representations.
    # SecretStr fields will be masked at this step.
    result = model.model_dump(mode="json")

    for field_name, _ in model.__class__.model_fields.items():
        value = getattr(model, field_name)
        if isinstance(value, SecretStr):
            result[field_name] = value.get_secret_value()

    return result


async def _ensure_team_members(
    storage: "StorageBase",
    user_id: str,
    team: "TeamRecord",
) -> list[TeamMember]:
    """Return the team's members, lazily migrating legacy ``member_ids``.

    Before ``AgentInvite`` existed, ``TeamData`` only stored a flat list
    of worker agent ids in :attr:`~TeamData.member_ids`; each such worker
    was ``source='team'`` with a 1:1 agent-to-session mapping, so the
    session could be recovered on demand via ``list_sessions``. With
    invited members the shape no longer fits: an invited agent has
    multiple sessions of its own, only one of which belongs to the team.

    This helper is the single read path. If :attr:`TeamData.members` is
    already populated it is returned verbatim (idempotent fast path).
    Otherwise, it rebuilds the roster from ``member_ids`` — each legacy
    entry is tagged ``role="created"`` — persists it back via
    :meth:`StorageBase.upsert_team`, and returns the new list. A
    ``member_id`` whose sole session has been deleted out from under
    the team is dropped from the migrated roster (nothing to route to
    anyway).

    Args:
        storage (`StorageBase`):
            Storage backend used for the session lookups and the
            optional writeback.
        user_id (`str`):
            The team owner.
        team (`TeamRecord`):
            The team record to inspect (and mutate in place on migration).

    Returns:
        `list[TeamMember]`:
            The team's current roster.
    """
    if team.data.members:
        return team.data.members

    if not team.data.member_ids:
        return []

    migrated: list[TeamMember] = []
    for agent_id in team.data.member_ids:
        sessions = await storage.list_sessions(user_id, agent_id)
        if not sessions:
            # Session already deleted — nothing sensible to route to.
            continue
        migrated.append(
            TeamMember(
                owner_id=user_id,
                agent_id=agent_id,
                session_id=sessions[0].id,
                role="created",
            ),
        )

    team.data.members = migrated
    # Sync the legacy list to match. Dropping entries with no surviving
    # session from ``members`` without also removing them from
    # ``member_ids`` would leave the two disagreeing, and would cause
    # this migration to re-run on every future read whenever the
    # migrated result is empty (empty ``members`` fails the fast-path
    # truthiness check, so we would fall through here again). Writing
    # ``member_ids`` to match ``migrated`` keeps the record consistent
    # and terminates the migration.
    team.data.member_ids = [m.agent_id for m in migrated]
    await storage.upsert_team(user_id, team)
    return migrated


@dataclass(frozen=True)
class _TeamLeader:
    """A team's leader: its session id plus its agent record."""

    session_id: str
    agent: "AgentRecord"

    @property
    def name(self) -> str:
        """The leader's current display name.

        Returns:
            `str`: The leader agent's name, as read this call.
        """
        return self.agent.data.name


async def _resolve_team_leader(
    storage: "StorageBase",
    user_id: str,
    team: "TeamRecord",
) -> "_TeamLeader | None":
    """Resolve *team*'s leader.

    The single read path for the leader's identity, shared by the team
    tools, the chat run context and the session router. Teams created
    with :attr:`TeamRecord.leader_agent_id` go straight to the agent
    record; older ones pay one extra session read every time, which
    beats writing to the team record on what are mostly read paths
    (including a GET endpoint) for one saved lookup. The name is always
    read fresh — an agent can be renamed at any time and ``TeamSay``
    routes by name.

    Args:
        storage (`StorageBase`):
            Storage backend for the lookups.
        user_id (`str`):
            The team owner (also the leader agent's owner).
        team (`TeamRecord`):
            The team whose leader to resolve.

    Returns:
        `_TeamLeader | None`:
            The leader's session id and agent record, or ``None`` when
            either is missing — an inconsistent state, since deleting a
            leader session or agent dissolves the team.
    """
    agent_id = team.leader_agent_id
    if agent_id is None:
        session = await storage.get_session(user_id, "", team.session_id)
        if session is None:
            return None
        agent_id = session.agent_id

    agent = await storage.get_agent(user_id, agent_id)
    if agent is None:
        return None
    return _TeamLeader(session_id=team.session_id, agent=agent)
