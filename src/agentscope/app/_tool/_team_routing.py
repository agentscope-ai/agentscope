# -*- coding: utf-8 -*-
"""Shared display-name routing helpers for team members."""

from typing import TYPE_CHECKING

from ._constants import HANDLE_LEN
from ..storage._utils import _ensure_team_members

if TYPE_CHECKING:
    from ..storage import StorageBase, TeamMember, TeamRecord


def _display_handle(agent_id: str) -> str:
    """Return the routing handle derived from an agent id.

    Args:
        agent_id (`str`):
            The agent id to shorten.

    Returns:
        `str`: The stable display handle used by team tools.
    """
    return agent_id[:HANDLE_LEN]


def _invited_display_name(agent_name: str, agent_id: str) -> str:
    """Format an invited member's leader-facing routing name.

    Args:
        agent_name (`str`):
            The invited agent's configured name.
        agent_id (`str`):
            The invited agent's id.

    Returns:
        `str`: A ``"<name>@<handle>"`` display string.
    """
    return f"{agent_name}@{_display_handle(agent_id)}"


async def _build_member_directory(
    storage: "StorageBase",
    user_id: str,
    team: "TeamRecord",
) -> dict[str, "TeamMember"]:
    """Build the common display-name directory for team members.

    Created members are addressed by their plain agent name. Invited
    members are addressed by ``"<name>@<handle>"``. Missing agent
    records are omitted because they cannot be routed to or removed by
    display name.

    Args:
        storage (`StorageBase`):
            Storage used to resolve the current roster and agent names.
        user_id (`str`):
            The team owner's user id.
        team (`TeamRecord`):
            The team whose members should be indexed.

    Returns:
        `dict[str, TeamMember]`:
            Display names mapped to their current roster entries.
    """
    directory: dict[str, "TeamMember"] = {}
    members = await _ensure_team_members(storage, user_id, team)
    for member in members:
        member_agent = await storage.get_agent(
            member.owner_id,
            member.agent_id,
        )
        if member_agent is None:
            continue
        display = member_agent.data.name
        if member.role == "invited":
            display = _invited_display_name(display, member.agent_id)
        directory[display] = member
    return directory
