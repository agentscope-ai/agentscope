# -*- coding: utf-8 -*-
"""The tool group class."""
from typing import Literal, Sequence

from ..mcp import MCPClient
from ._base import ToolBase
from ..skill import SkillLoaderBase, Skill, LocalSkillLoader


class ToolGroup:
    """A group of related tools, mcps, and skills that an agent can activate,
    deactivate and use together. The tool group is activated by the meta tool
    `ResetTools`.

    In high-code scenarios, the tools argument accepts any child classes for
    ToolBase class, and the tool groups supports serialization.
    """

    name: Literal["basic"] | str
    """Note the "basic" group is special and represents the default tool group
    that will be always be activated for the agent."""

    description: str
    """A description of the tool group from an agent-oriented perspective,
    outlining its capabilities and the conditions under which it should be
    activated."""

    instructions: str | None
    """Instructions included in the meta tool's result upon activation of
    this tool group, guiding the agent on how to properly use the meta tool."""

    tools: list[ToolBase]
    """The tools in this group."""

    skills_or_loaders: list[Skill | SkillLoaderBase]
    """The skills in this group."""

    mcps: list[MCPClient]
    """The mcps in this group."""

    def __init__(
        self,
        name: Literal["basic"] | str,
        description: str | None = None,
        instructions: str | None = None,
        tools: list[ToolBase] | None = None,
        skills_or_loaders: Sequence[str | Skill | SkillLoaderBase]
        | None = None,
        mcps: list[MCPClient] | None = None,
    ) -> None:
        """Initialize the tool group.

        Args:
            name (`Literal["basic"] | str`):
                The name of the tool group.
            description (`str | None`):
                The description of the tool group.
            instructions (`str | None`, optional):
                Instructions included in the meta tool's result upon
                activation of this tool group, guiding the agent on how to
                properly use the meta tool.
            tools (`list[ToolBase] | None`, optional):
                The tools in this group.
            skills_or_loaders (`list[str | Skill | SkillLoaderBase] | None`, \
            optional):
                The skill paths, data, and loaders to access skills in this
                group.
            mcps (`list[MCPClient] | None`, optional):
                The mcps in this group.
        """
        if name != "basic" and description is None:
            raise ValueError(
                f"The tool group description is required for tool group "
                f"'{name}' (Only the 'basic' tool group can have an optional "
                f"description).",
            )

        self.name = name
        self.description = description or ""
        self.instructions = instructions
        self.tools = tools or []
        self.mcps = mcps or []

        # Skill
        self.skills_or_loaders = []
        for _ in skills_or_loaders or []:
            if isinstance(_, str):
                self.skills_or_loaders.append(LocalSkillLoader(directory=_))

            elif isinstance(_, (Skill, SkillLoaderBase)):
                self.skills_or_loaders.append(_)

            else:
                raise TypeError(
                    f"Invalid skill or loader: {_}. Must be a skill, "
                    f"skill loader, or directory path.",
                )

    async def list_skills(self) -> list[Skill]:
        """List all the skills in this tool group."""
        skills = []
        for skill_or_loader in self.skills_or_loaders:
            if isinstance(skill_or_loader, Skill):
                skills.append(skill_or_loader)
            elif isinstance(skill_or_loader, SkillLoaderBase):
                skills.extend(await skill_or_loader.list_skills())

        return skills
