# -*- coding: utf-8 -*-
"""The skill loader base class."""
from abc import abstractmethod, ABC
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    """The agent skill class"""

    name: str
    """The name of the skill."""
    description: str
    """The description of the skill."""
    dir: str
    """The directory of the agent skill."""
    markdown: str
    """The markdown content of the agent skill."""
    updated_at: float
    """The last updated time of the skill."""
    metadata: dict = field(default_factory=dict)
    """The YAML frontmatter metadata parsed from SKILL.md."""
    resources: dict[str, str] = field(default_factory=dict)
    """In-memory map of resource paths to content (e.g. scripts, references)."""

    @property
    def skill_id(self) -> str:
        """Unique identifier: name + source directory basename."""
        source = Path(self.dir).name
        return f"{self.name}_{source}"


class SkillLoaderBase(ABC):
    """The base class for skill loader."""

    @abstractmethod
    async def list_skills(self) -> list[Skill]:
        """List all the skills that can be loaded by this loader."""
        raise NotImplementedError
