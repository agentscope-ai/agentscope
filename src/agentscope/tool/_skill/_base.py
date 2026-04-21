# -*- coding: utf-8 -*-
"""The skill loader base class."""
from abc import abstractmethod

from .._types import Skill


class SkillLoaderBase:
    """The base class for skill loader."""

    @abstractmethod
    async def list_skills(self) -> list[Skill]:
        """List all the skills that can be loaded by this loader."""
        raise NotImplementedError
