# -*- coding: utf-8 -*-
"""外部 skill 端点响应模型（迁移自 ``bankcomm_adp.routers._schema``）。"""
from __future__ import annotations

from pydantic import BaseModel, Field


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
    """Response body for the external skill list endpoints."""

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
