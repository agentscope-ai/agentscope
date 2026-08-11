# -*- coding: utf-8 -*-
"""Shared display-name routing helpers for team members."""

from collections import Counter
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
    reserved_names: set[str] | None = None,
) -> dict[str, "TeamMember"]:
    """Build the common display-name directory for team members.

    Created members are addressed by their plain agent name. Invited
    members are addressed by ``"<name>@<handle>"``. When renamed agents
    collide with another member or a reserved name, the affected member
    receives a stable handle-qualified display name instead of silently
    overwriting an existing directory entry. Missing agent records are
    omitted because they cannot be routed to or removed by display name.

    Args:
        storage (`StorageBase`):
            Storage used to resolve the current roster and agent names.
        user_id (`str`):
            The team owner's user id.
        team (`TeamRecord`):
            The team whose members should be indexed.
        reserved_names (`set[str] | None`, optional):
            Display names already occupied by non-members, such as the
            team leader. Defaults to ``None``.

    Returns:
        `dict[str, TeamMember]`:
            Display names mapped to their current roster entries.
    """
    reserved = reserved_names or set()
    members = await _ensure_team_members(storage, user_id, team)
    entries: list[tuple[str, str, str, "TeamMember"]] = []
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
        qualified = _invited_display_name(
            member_agent.data.name,
            member.agent_id,
        )
        entries.append(
            (member_agent.data.name, display, qualified, member),
        )

    display_counts = Counter(display for _, display, _, _ in entries)
    qualified_counts = Counter(qualified for _, _, qualified, _ in entries)
    directory: dict[str, "TeamMember"] = {}
    for agent_name, display, qualified, member in entries:
        if display_counts[display] == 1 and display not in reserved:
            resolved = display
        elif (
            qualified_counts[qualified] == 1
            and qualified not in reserved
            and qualified not in directory
        ):
            resolved = qualified
        else:
            resolved = f"{agent_name}@{member.agent_id}"
            if resolved in reserved or resolved in directory:
                stem = f"{resolved}:{member.session_id}"
                resolved = stem
                suffix = 2
                while resolved in reserved or resolved in directory:
                    resolved = f"{stem}:{suffix}"
                    suffix += 1
        directory[resolved] = member
    return directory
