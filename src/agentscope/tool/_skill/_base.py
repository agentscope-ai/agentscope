# -*- coding: utf-8 -*-
"""The skill loader base class."""
from abc import abstractmethod, ABC

from .._types import Skill


class SkillLoaderBase(ABC):
    """The base class for skill loader."""

    @abstractmethod
    async def list_skills(self) -> list[Skill]:
        """List all the skills that can be loaded by this loader."""
        raise NotImplementedError
