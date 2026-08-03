# -*- coding: utf-8 -*-
"""Turning an agent's bound MCPs and skills into workspace seeds."""
from typing import NamedTuple

from ..hub import HubSkillSource, SkillHubBase
from ..storage import StorageBase
from ...mcp import MCPClient
from ...skill import SkillSourceBase


class AgentSeeds(NamedTuple):
    """What an agent brings to a workspace that does not exist yet.

    Attributes:
        mcps: Connectable clients, ready for ``default_mcps``.
        skills: Deferred downloads, ready for ``default_skills``.
        errors: Bindings that could not be resolved at all, mapped to
            why. Distinct from a seed that failed to install: these
            never became a seed, and re-deriving them costs two reads,
            so they are reported live rather than remembered.
    """

    mcps: list[MCPClient]
    skills: list[SkillSourceBase]
    errors: dict[str, str]


async def resolve_agent_seeds(
    storage: StorageBase,
    skill_hubs: dict[str, SkillHubBase],
    user_id: str,
    agent_id: str,
) -> AgentSeeds:
    """Resolve an agent's bound library ids into workspace seeds.

    Reads the library wholesale rather than per id: the number of
    bindings varies, the number of round trips should not.

    A binding the caller cannot see — because the record is gone, or
    because the agent is shared and the ids belong to its owner — is
    reported rather than raised. The other seeds still go in, and
    failing to open a workspace over one stale id would be worse than
    opening it short a tool.

    Args:
        storage (`StorageBase`):
            Where the user's library lives.
        skill_hubs (`dict[str, SkillHubBase]`):
            Registered skill hubs, keyed by id.
        user_id (`str`):
            The user whose library the ids are looked up in.
        agent_id (`str`):
            The agent whose bindings to resolve.

    Returns:
        `AgentSeeds`:
            The seeds, plus whatever could not be resolved.
    """
    agent = await storage.get_agent(user_id, agent_id)
    if agent is None or not (agent.data.mcp_ids or agent.data.skill_ids):
        return AgentSeeds([], [], {})

    mcps: list[MCPClient] = []
    errors: dict[str, str] = {}
    if agent.data.mcp_ids:
        by_id = {r.id: r for r in await storage.list_mcps(user_id)}
        for mcp_id in agent.data.mcp_ids:
            record = by_id.get(mcp_id)
            if record is None:
                errors[mcp_id] = "No longer in your library."
            elif record.enabled:
                mcps.append(record.client)

    skills: list[SkillSourceBase] = []
    if agent.data.skill_ids:
        by_id = {r.id: r for r in await storage.list_skills(user_id)}
        for skill_id in agent.data.skill_ids:
            record = by_id.get(skill_id)
            if record is None:
                errors[skill_id] = "No longer in your library."
                continue
            if not record.enabled:
                continue
            hub = skill_hubs.get(record.hub_id or "")
            if hub is None:
                errors[
                    record.name
                ] = f"Its hub {record.hub_id!r} is no longer registered."
                continue
            skills.append(
                HubSkillSource(
                    hub,
                    user_id,
                    record.card_id or record.name,
                    record.name,
                    record.version,
                ),
            )

    return AgentSeeds(mcps, skills, errors)
