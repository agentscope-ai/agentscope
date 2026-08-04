# -*- coding: utf-8 -*-
"""Schemas for equipping a workspace with MCPs and skills."""
from pydantic import BaseModel, Field

from ....mcp import MCPClient


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


class SkillInfo(BaseModel):
    """One skill listing served by the agent-skills endpoint."""

    name: str = Field(description="The skill name (slug).")
    category: str = Field(default="public", description="The skill category.")
    description: str = Field(
        default="",
        description="The user-facing description of the skill.",
    )
    used: bool = Field(
        default=False,
        description="Whether the caller already installed this skill.",
    )


class AgentSkillsListResponse(BaseModel):
    """Response body for ``GET /workspace/agents/{name}/skills``."""

    skills: list[SkillInfo] = Field(
        default_factory=list,
        description="The skills on this page.",
    )
    total: int = Field(
        default=0,
        description="The number of skills returned.",
    )


class SkillActionResponse(BaseModel):
    """Response body for the skill enable/download action."""

    success: bool = Field(description="Whether the action succeeded.")
    action: str = Field(description="The action performed, e.g. 'enabled'.")
    skill_id: str = Field(
        description="The skill identifier, 'category:name'.",
    )
