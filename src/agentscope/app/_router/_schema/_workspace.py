# -*- coding: utf-8 -*-
"""Schemas for equipping a workspace with MCPs and skills."""
from pydantic import BaseModel, Field

from ....mcp import MCPClient
from ....skill import Skill


class AddSkillRequest(BaseModel):
    """The request to add skill."""

    skill_path: str


class AddFromLibraryRequest(BaseModel):
    """The request to put library MCPs into a workspace."""

    mcp_ids: list[str] = Field(
        description="The installed-MCP record ids to add.",
    )


class AddSkillsFromLibraryRequest(BaseModel):
    """The request to put library skills into a workspace."""

    skill_ids: list[str] = Field(
        description="The installed-skill record ids to add.",
    )


class AddFromLibraryResponse(BaseModel):
    """What landed, and what did not.

    Reported per item rather than as one status: installing is done one
    at a time, so a bad API key on the third pick must not throw away
    the two that worked.
    """

    added: list[str] = Field(
        default_factory=list,
        description=(
            "The names now in the workspace. Excludes ones already "
            "present, which are skipped rather than re-added."
        ),
    )
    failed: dict[str, str] = Field(
        default_factory=dict,
        description="Whatever could not be added, mapped to why.",
    )


class _SeededResponse(BaseModel):
    """What the agent came with but could not be given.

    Populated when the workspace is first created — an MCP that would
    not connect, a skill whose hub is gone, a binding pointing at a
    library record that was deleted. Reported here rather than as an
    error status because the workspace itself is fine; it is simply
    missing something the agent was configured with.
    """

    seed_errors: dict[str, str] = Field(
        default_factory=dict,
        description="Names of the agent's own MCPs / skills that are "
        "not in this workspace, mapped to why.",
    )


class ListWorkspaceMCPsResponse(_SeededResponse):
    """The MCPs present in a workspace."""

    mcps: list["MCPClientStatus"] = Field(default_factory=list)


class ListWorkspaceSkillsResponse(_SeededResponse):
    """The skills present in a workspace."""

    skills: list[Skill] = Field(default_factory=list)


class ToolInfo(BaseModel):
    """The tool info."""

    name: str
    description: str | None = None


class MCPClientStatus(MCPClient):
    """MCPClient enriched with live tool list and health status."""

    is_healthy: bool = False
    tools: list[ToolInfo] = Field(default_factory=list)
    error: str | None = Field(
        default=None,
        description=(
            "Why listing this MCP's tools failed. A red dot alone leaves "
            "the user with nothing to act on — a wrong API key, an "
            "unreachable host and a missing command all look the same."
        ),
    )


ListWorkspaceMCPsResponse.model_rebuild()
