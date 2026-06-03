# -*- coding: utf-8 -*-
"""The team storage class."""
from pydantic import BaseModel, Field

from ._base import _RecordBase


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
            "Worker agent ids that belong to this team. Each worker has "
            "``source='team'`` and exactly one session, so the agent id "
            "uniquely identifies the member; the session can be looked up "
            "via :meth:`StorageBase.list_sessions`."
        ),
        title="Member Ids",
    )


class TeamRecord(_RecordBase):
    """The team ORM model.

    Team membership is session-level: the leader is identified by its
    ``session_id`` (since a user agent can lead multiple teams across
    different sessions). Workers are identified by their agent id in
    :attr:`TeamData.member_ids` (since workers have a 1:1 mapping between
    agent and session).
    """

    user_id: str
    """The user id."""

    session_id: str
    """The leader session id — the session that called ``create_team``."""

    data: TeamData
    """The team data."""
