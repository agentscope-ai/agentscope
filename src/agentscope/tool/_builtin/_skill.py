# -*- coding: utf-8 -*-
"""The builtin skill viewer tool."""
from typing import Any, Callable, Awaitable

from .._permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .._response import ToolChunk
from .._base import ToolBase
from .._types import Skill
from ...message import TextBlock


class SkillViewer(ToolBase):
    """The builtin skill viewer tool."""

    name: str = "Skill"
    """The name of the skill viewer tool to the agent."""

    description = (
        "The skill viewer tool is used to view the details of the skills "
        "that the agent has. It can be used to view the name, description, "
        "and parameters of the skills."
    )
    """The tool description of the skill viewer tool to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "The name of the skill to be viewed.",
            },
        },
        "required": ["skill_name"],
    }
    """The input schema of the skill viewer tool."""

    is_concurrency_safe: bool = True
    """The skill viewer is concurrency safe."""

    is_read_only: bool = True
    """The skill viewer is read-only."""

    is_mcp: bool = False
    """The skill viewer is not an MCP tool."""

    mcp_name: str | None = None
    """The skill viewer does not belong to any MCP server."""

    def __init__(
        self,
        get_skills_method: Callable[..., Awaitable[dict[str, Skill]]],
    ) -> None:
        """Initialize the skill viewer with the list of skills.

        Args:
            get_skills_method (`Callable[..., dict[str, Skill]]`):
                An async method that returns the current skills of the agent.
        """
        self._get_skills_method = get_skills_method

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """The skill viewer is always allowed to be called."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="The skill viewer is always allowed to be called.",
        )

    async def __call__(self, skill_name: str) -> ToolChunk:
        """View the details of the skill with the given name.

        Args:
            skill_name (`str`):
                The name of the skill to be viewed.

        Returns:
            `ToolChunk`:
                The details of the skill.
        """

        skills = await self._get_skills_method()
        skill = skills.get(skill_name)
        if not skill:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"SkillNotFoundError: Skill '{skill_name}' "
                        f"not found.",
                    ),
                ],
            )

        return ToolChunk(content=[TextBlock(text=skill.markdown)])
