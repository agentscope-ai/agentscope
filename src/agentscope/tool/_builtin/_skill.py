# -*- coding: utf-8 -*-
"""The builtin skill tools."""
import asyncio
import os
from typing import Any, Awaitable, Callable, List

from ...exception import DeveloperOrientedException
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)
from .._response import ToolChunk
from .._base import ToolBase, ToolMiddlewareBase
from ...skill import Skill
from ...message import TextBlock, ToolResultState
from ...state import AgentState


class SkillViewer(ToolBase):
    """The builtin skill viewer tool."""

    name: str = "Skill"
    """The name of the skill viewer tool to the agent."""

    description = (
        "Retrieve a skill within the conversation. "
        "When users asks you to perform tasks, check if any of the available "
        "skills match. "
        "Skills provide specialized capabilities and domain knowledge."
    )
    """The tool description of the skill viewer tool to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "The exact name of the skill to view. ",
            },
        },
        "required": ["skill"],
    }
    """The input schema of the skill viewer tool."""

    is_concurrency_safe: bool = True
    """The skill viewer is concurrency safe."""

    is_external_tool: bool = False
    """The skill viewer is not an external tool."""

    is_state_injected: bool = True
    """The skill viewer require state injection to access the activated tool
    group."""

    is_read_only: bool = True
    """The skill viewer is read-only."""

    is_mcp: bool = False
    """The skill viewer is not an MCP tool."""

    mcp_name: str | None = None
    """The skill viewer does not belong to any MCP server."""

    def __init__(
        self,
        get_skills_method: Callable[..., Awaitable[dict[str, Skill]]],
        middlewares: List[ToolMiddlewareBase] | None = None,
    ) -> None:
        """Initialize the skill viewer with the list of skills.

        Args:
            get_skills_method (`Callable[..., dict[str, Skill]]`):
                An async method that returns the current skills of the agent.
            middlewares (`List[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
        """
        super().__init__(middlewares=middlewares)
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

    async def call(
        self,
        skill: str,
        _agent_state: AgentState,
    ) -> ToolChunk:
        """View the details of the skill with the given name.

        Args:
            skill (`str`):
                The name of the skill to be viewed.

        Returns:
            `ToolChunk`:
                The details of the skill.
        """
        if not isinstance(_agent_state, AgentState):
            raise DeveloperOrientedException(
                f"Expected AgentState but got {type(_agent_state)} "
                "instead for the Skill viewer tool.",
            )

        # View the activated skills
        skills = await self._get_skills_method(
            _agent_state.tool_context.activated_groups,
        )
        target_skill = skills.get(skill)
        if not target_skill:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"SkillNotFoundError: Skill '{skill}' "
                        f"not found.",
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        return ToolChunk(content=[TextBlock(text=target_skill.markdown)])


class SkillMarkdownReader(ToolBase):
    """The builtin SKILL.md reader tool."""

    name: str = "read_skill_md"
    """The name of the skill markdown reader tool to the agent."""

    description = "Read the SKILL.md file for a registered skill."
    """The tool description of the skill markdown reader tool."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": (
                    "The exact name of the skill whose SKILL.md should "
                    "be read."
                ),
            },
        },
        "required": ["skill"],
    }
    """The input schema of the skill markdown reader tool."""

    is_concurrency_safe: bool = True
    """The skill markdown reader is concurrency safe."""

    is_external_tool: bool = False
    """The skill markdown reader is not an external tool."""

    is_state_injected: bool = True
    """The skill markdown reader requires state injection to access skills."""

    is_read_only: bool = True
    """The skill markdown reader is read-only."""

    is_mcp: bool = False
    """The skill markdown reader is not an MCP tool."""

    mcp_name: str | None = None
    """The skill markdown reader does not belong to any MCP server."""

    def __init__(
        self,
        get_skills_method: Callable[..., Awaitable[dict[str, Skill]]],
        middlewares: List[ToolMiddlewareBase] | None = None,
    ) -> None:
        """Initialize the skill markdown reader.

        Args:
            get_skills_method (`Callable[..., dict[str, Skill]]`):
                An async method that returns the current skills of the agent.
            middlewares (`List[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
        """
        super().__init__(middlewares=middlewares)
        self._get_skills_method = get_skills_method

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """The skill markdown reader is always allowed to be called."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=(
                "The skill markdown reader is always allowed to be called."
            ),
        )

    async def call(
        self,
        skill: str,
        _agent_state: AgentState,
    ) -> ToolChunk:
        """Read the SKILL.md file for the skill with the given name.

        Args:
            skill (`str`):
                The name of the skill whose SKILL.md should be read.

        Returns:
            `ToolChunk`:
                The raw SKILL.md file content.
        """
        if not isinstance(_agent_state, AgentState):
            raise DeveloperOrientedException(
                f"Expected AgentState but got {type(_agent_state)} "
                "instead for the skill markdown reader tool.",
            )

        skills = await self._get_skills_method(
            _agent_state.tool_context.activated_groups,
        )
        target_skill = skills.get(skill)
        if not target_skill:
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"SkillNotFoundError: Skill '{skill}' "
                        f"not found.",
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        skill_root = os.path.abspath(os.path.expanduser(target_skill.dir))
        skill_md_path = os.path.join(skill_root, "SKILL.md")

        if not os.path.isfile(skill_md_path):
            return ToolChunk(
                content=[
                    TextBlock(
                        text="SkillFileNotFoundError: SKILL.md not found "
                        f"for skill '{skill}'.",
                    ),
                ],
                state=ToolResultState.ERROR,
            )

        def _read_skill_md() -> str:
            with open(skill_md_path, encoding="utf-8") as f:
                return f.read()

        return ToolChunk(
            content=[
                TextBlock(text=await asyncio.to_thread(_read_skill_md)),
            ],
        )
