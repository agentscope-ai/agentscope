# -*- coding: utf-8 -*-
"""The skill loader base class."""
from abc import abstractmethod, ABC
from dataclasses import dataclass


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


class SkillLoaderBase(ABC):
    """The base class for skill loader."""

    @abstractmethod
    async def list_skills(self) -> list[Skill]:
        """List all the skills that can be loaded by this loader."""
        raise NotImplementedError
